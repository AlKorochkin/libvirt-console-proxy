#!/usr/bin/env python3
import argparse
from flask import Flask, render_template
from flask_socketio import SocketIO
import pty
import os
import subprocess
import select
import termios
import struct
import fcntl
import shlex
import logging
import sys

from settings import URI, UUID
from consolecallback import Console
import libvirt

logging.getLogger("werkzeug").setLevel(logging.ERROR)

__version__ = "0.5.0.2"

app = Flask(__name__, template_folder=".", static_folder=".", static_url_path="")
app.config["SECRET_KEY"] = "secret!"
app.config["fd"] = None
# app.config["child_pid"] = None
console_connections = list()
socketio = SocketIO(app)

def set_winsize(fd, row, col, xpix=0, ypix=0):
    logging.debug("setting window size with termios")
    winsize = struct.pack("HHHH", row, col, xpix, ypix)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)

def error_handler(unused, error) -> None:
    # The console stream errors on VM shutdown; we don't care
    if error[0] == libvirt.VIR_ERR_RPC and error[1] == libvirt.VIR_FROM_STREAMS:
        return
    logging.warning(error)


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
        socketio.emit("pty-output", {"output": received_data.decode()}, namespace="/pty")
    except Exception:
        return
    #os.write(0, received_data) # дублировать в консоль


def lifecycle_callback(connection: libvirt.virConnect, domain: libvirt.virDomain, event: int, detail: int, console: Console) -> None:
    console.state = console.domain.state(0)
    logging.info("%s transitioned to state %d, reason %d",
                 console.uuid, console.state[0], console.state[1])


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

# def read_and_forward_pty_output():
#     max_read_bytes = 1024 * 20
#     while True:
#         socketio.sleep(0.01)
#         if app.config["fd"]:
#             timeout_sec = 0
#             (data_ready, _, _) = select.select([app.config["fd"]], [], [], timeout_sec)
#             if data_ready:
#                 output = os.read(app.config["fd"], max_read_bytes).decode(
#                     errors="ignore"
#                 )
#                 socketio.emit("pty-output", {"output": output}, namespace="/pty")

def read_and_forward_pty_output():
    max_read_bytes = 1024 * 20
    while check_console(console_connections[0]):
        libvirt.virEventRunDefaultImpl()


@app.route("/")
def index():
    return render_template("index.html")


# @socketio.on("pty-input", namespace="/pty")
# def pty_input(data):
#     """write to the child pty. The pty sees this as if you are typing in a real
#     terminal.
#     """
#     if app.config["fd"]:
#         logging.debug("received input from browser: %s" % data["input"])
#         os.write(app.config["fd"], data["input"].encode())

@socketio.on("pty-input", namespace="/pty")
def pty_input(data):
    """write to the child pty. The pty sees this as if you are typing in a real
    terminal.
    """
    logging.debug("received input from browser: %s" % data["input"])
    #os.write(app.config["fd"], data["input"].encode())
    console_connections[0].stream.send(data["input"].encode())


@socketio.on("resize", namespace="/pty")
def resize(data):
    if app.config["fd"]:
        logging.debug(f"Resizing window to {data['rows']}x{data['cols']}")
        set_winsize(app.config["fd"], data["rows"], data["cols"])


# @socketio.on("connect", namespace="/pty")
# def connect():
#     """new client connected"""
#     logging.info("new client connected")
#     if app.config["child_pid"]:
#         # already started child process, don't start another
#         return

#     # create child process attached to a pty we can read from and write to
#     (child_pid, fd) = pty.fork()
#     if child_pid == 0:
#         # this is the child process fork.
#         # anything printed here will show up in the pty, including the output
#         # of this subprocess
#         subprocess.run(app.config["cmd"])
#     else:
#         # this is the parent process fork.
#         # store child fd and pid
#         app.config["fd"] = fd
#         app.config["child_pid"] = child_pid
#         set_winsize(fd, 50, 50)
#         cmd = " ".join(shlex.quote(c) for c in app.config["cmd"])
#         # logging/print statements must go after this because... I have no idea why
#         # but if they come before the background task never starts
#         socketio.start_background_task(target=read_and_forward_pty_output)

#         logging.info("child pid is " + child_pid)
#         logging.info(
#             f"starting background task with command `{cmd}` to continously read "
#             "and forward pty output to client"
#         )
#         logging.info("task started")

@socketio.on("connect", namespace="/pty")
def connect():
    """new client connected"""
    logging.info("new client connected")
    # if app.config["child_pid"]:
    #     # already started child process, don't start another
    #     return

    # create child process attached to a pty we can read from and write to
    libvirt.virEventRegisterDefaultImpl()
    libvirt.registerErrorHandler(error_handler, None)
    console = Console(URI, UUID)
    console.stdin_watch = libvirt.virEventAddHandle(0, libvirt.VIR_EVENT_HANDLE_READABLE, stdin_callback, console)
    console_connections.append(console)

    # set_winsize(fd, 50, 50)
    # logging/print statements must go after this because... I have no idea why
    # but if they come before the background task never starts
    socketio.start_background_task(target=read_and_forward_pty_output)

    logging.info("task started")

def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-p", "--port", default=5000, help="port to run server on", type=int
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="host to run server on (use 0.0.0.0 to allow access from other hosts)",
    )
    parser.add_argument("--debug", action="store_true", help="debug the server")
    parser.add_argument("--version", action="store_true", help="print version and exit")
    parser.add_argument(
        "--command", default="bash", help="Command to run in the terminal"
    )
    parser.add_argument(
        "--cmd-args",
        default="",
        help="arguments to pass to command (i.e. --cmd-args='arg1 arg2 --flag')",
    )
    args = parser.parse_args()
    if args.version:
        print(__version__)
        exit(0)
    app.config["cmd"] = [args.command] + shlex.split(args.cmd_args)
    green = "\033[92m"
    end = "\033[0m"
    log_format = (
        green
        + "pyxtermjs > "
        + end
        + "%(levelname)s (%(funcName)s:%(lineno)s) %(message)s"
    )
    logging.basicConfig(
        format=log_format,
        stream=sys.stdout,
        level=logging.DEBUG if args.debug else logging.INFO,
    )
    logging.info(f"serving on http://{args.host}:{args.port}")
    socketio.run(app, debug=args.debug, port=args.port, host=args.host)


if __name__ == "__main__":
    main()
