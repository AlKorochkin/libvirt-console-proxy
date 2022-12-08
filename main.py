from settings import URI, UUID
from consolecallback import Console
import libvirt
import socketio
import time
import logging
import sys
import pty
import os
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.wsgi import WSGIMiddleware

logger = logging.getLogger(__name__)

app = FastAPI()
console_connections = dict()
sio = socketio.Server(cors_allowed_origins="*")
_app = socketio.WSGIApp(socketio_app=sio, socketio_path="")
app.mount("/socket.io", WSGIMiddleware(_app))

class SocketioUserSessionStorage():
    def __init__(self):
        self.storage = dict()

    def save(self, sid, sdata):
        if sid not in self.storage:
            self.storage[sid] = sdata
        else:
            self.storage[sid].update(sdata)
    
    def get(self, sid):
        if sid not in self.storage:
            return None
        return self.storage[sid]

session_storage = SocketioUserSessionStorage()

def error_handler(unused, error) -> None:
    # The console stream errors on VM shutdown; we don't care
    if error[0] == libvirt.VIR_ERR_RPC and error[1] == libvirt.VIR_FROM_STREAMS:
        return
    logger.warning(error)


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
        sio.emit("pty-output", {"output": received_data.decode()}, namespace="/pty")
    except Exception as e:
        return


def lifecycle_callback(connection: libvirt.virConnect, domain: libvirt.virDomain, event: int, detail: int, console: Console) -> None:
    console.state = console.domain.state(0)


def check_console(console: Console) -> bool:
    if (console.state[0] == libvirt.VIR_DOMAIN_RUNNING or console.state[0] == libvirt.VIR_DOMAIN_PAUSED):
        if console.stream is None:
            console.stream = console.connection.newStream(libvirt.VIR_STREAM_NONBLOCK)
            # падает при попытке подключиться к одной консоли из двух сеансов(только одно подключение)
            opened = console.domain.openConsole(None, console.stream, 0)
            console.stream.eventAddCallback(libvirt.VIR_STREAM_EVENT_READABLE, stream_callback, console)
    else:
        if console.stream:
            console.stream.eventRemoveCallback()
            console.stream = None
    return console.run_console


def console_event_handler(sid):
    #session = sio.get_session(sid)
    session = session_storage.get(sid)
    console = session["console"]
    while check_console(console):
        libvirt.virEventRunDefaultImpl()

@app.get("/")
def read_index():
    with open("index.html", 'r') as fd:
        html_content = fd.read()
    return HTMLResponse(html_content)


@sio.on("pty-input", namespace="/pty")
def pty_input(sid, data):
    """ 
        Write to stream.
    """
    logger.debug("received input from sid {}: {}".format(sid, data["input"]))
    #session = sio.get_session(sid)
    session = session_storage.get(sid)
    console = session["console"]
    console.stream.send(data["input"].encode())

@sio.on("connect", namespace="/pty")
def connect(sid, *args, **kwargs):
    """new client connected"""
    logging.info("new client connected")
    for arg in args:
        if "token" in arg:
            pass
            #user = get_user(token)
            #session_storage.save(sid, {"user": user})
        if "QUERY_STRING" in arg:
            query = arg["QUERY_STRING"].split("&")
            query_items = {x.split("=")[0]:x.split("=")[1] for x in query}
            if "nodename" in query_items:
                node_name = query_items["nodename"]
            else:
                return
    libvirt.virEventRegisterDefaultImpl()
    libvirt.registerErrorHandler(error_handler, None)
    console = Console(URI, node_name)
    console.stdin_watch = libvirt.virEventAddHandle(0, libvirt.VIR_EVENT_HANDLE_READABLE, stdin_callback, console)
    #sio.save_session(sid, {"console": console})
    session_storage.save(sid, {"console": console})
    sio.start_background_task(console_event_handler, sid)

if __name__ == '__main__':
    import logging
    import sys

    logging.basicConfig(level=logging.DEBUG,
                        stream=sys.stdout)
    import uvicorn
    uvicorn.run("main:app", host='0.0.0.0', port=5000, reload=True)