#!/usr/bin/env python3
# consolecallback - provide a persistent console that survives guest reboots

import os
import logging
import libvirt
import tty
import termios
import atexit
from argparse import ArgumentParser
from typing import Optional  # noqa F401


def reset_term() -> None:
    termios.tcsetattr(0, termios.TCSADRAIN, attrs)


def error_handler(unused, error) -> None:
    # The console stream errors on VM shutdown; we don't care
    if error[0] == libvirt.VIR_ERR_RPC and error[1] == libvirt.VIR_FROM_STREAMS:
        return
    logging.warning(error)


class Console(object):
    def __init__(self, uri: str, domain_name: str) -> None:
        self.uri = uri
        # self.uuid = uuid
        self.domain_name = domain_name
        self.connection = libvirt.open(uri)
        self.domain = self.connection.lookupByName(domain_name)
        self.state = self.domain.state(0)
        self.connection.domainEventRegister(lifecycle_callback, self)
        self.stream = None  # type: Optional[libvirt.virStream]
        self.run_console = True
        self.stdin_watch = -1


def check_console(console: Console) -> bool:
    if (console.state[0] == libvirt.VIR_DOMAIN_RUNNING or console.state[0] == libvirt.VIR_DOMAIN_PAUSED):
        if console.stream is None:
            console.stream = console.connection.newStream(libvirt.VIR_STREAM_NONBLOCK)
            opened = console.domain.openConsole(None, console.stream, 0)
            console.stream.eventAddCallback(libvirt.VIR_STREAM_EVENT_READABLE, stream_callback, console)
    else:
        if console.stream:
            console.stream.eventRemoveCallback()
            console.stream = None

    return console.run_console


def stdin_callback(watch: int, fd: int, events: int, console: Console) -> None:
    readbuf = os.read(fd, 1024)
    if readbuf.startswith(b''):
        console.run_console = False
        return
    if console.stream:
        console.stream.send(readbuf)

def stream_callback(stream: libvirt.virStream, events: int, console: Console) -> None:
    try:
        assert console.stream
        received_data = console.stream.recv(1024)
    except Exception:
        return
    os.write(0, received_data)


def lifecycle_callback(connection: libvirt.virConnect, domain: libvirt.virDomain, event: int, detail: int, console: Console) -> None:
    console.state = console.domain.state(0)



# main
if __name__ == "__main__":
    from settings import URI, UUID
    
    print("Escape character is ^]")
    logging.basicConfig(filename='msg.log', level=logging.DEBUG)
    logging.info("URI: %s", URI)
    logging.info("UUID: %s", UUID)

    libvirt.virEventRegisterDefaultImpl()
    libvirt.registerErrorHandler(error_handler, None)

    atexit.register(reset_term)
    attrs = termios.tcgetattr(0)
    tty.setraw(0)
    
    console = Console(URI, UUID)
    console.stdin_watch = libvirt.virEventAddHandle(0, libvirt.VIR_EVENT_HANDLE_READABLE, stdin_callback, console)

    while check_console(console):
        libvirt.virEventRunDefaultImpl()
