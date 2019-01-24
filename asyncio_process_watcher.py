#!/usr/bin/python3


import asyncio
import logging
import collections
import datetime
import pytz
import os
import aiohttp.web


async def handler_status(request):
    resp = request.app['process'].status()
    return aiohttp.web.Response(text=resp)


async def handler_logs(request):
    resp = request.app['process'].logs()
    return aiohttp.web.json_response(resp)


async def handler_start(request):
    resp = await request.app['process'].start()
    return aiohttp.web.Response(text=resp)


async def handler_stop(request):
    resp = await request.app['process'].stop()
    return aiohttp.web.Response(text=resp)


async def run_process(app):
    app['proc'] = await asyncio.create_subprocess_shell(
        app.cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


class MyProcess(object):


    def __init__(self, term_timeout=30, restart_delay=30, max_logs=100):
        self.term_timeout = term_timeout
        self.restart_delay = restart_delay
        self.logger_logs = logging.getLogger('process')
        self.logger_stdout = logging.getLogger('stdout')
        self.logger_stderr = logging.getLogger('stderr')
        self.logs_dq = collections.deque(maxlen=max_logs)
        self.stdout_dq = collections.deque(maxlen=max_logs)
        self.stderr_dq = collections.deque(maxlen=max_logs)


    @property
    def now_local(self):
        return datetime.datetime.now(pytz.timezone('Europe/Luxembourg')).replace(microsecond=0).isoformat()


    def logger(self, logger_name, logger_level, message):
        if logger_level == 'info':
            level = logging.INFO
        elif logger_level == 'warning':
            level = logging.WARNING
        elif logger_level == 'debug':
            level = logging.DEBUG
        else:
            level = logging.ERROR
        
        if logger_name == 'stdout':
            logger = self.logger_stdout
            dq = self.stdout_dq
        elif logger_name == 'stderr':
            logger = self.logger_stderr
            dq = self.stderr_dq
        else:
            logger = self.logger_logs
            dq = self.logs_dq
        if isinstance(message, str):
            message_str = message
        else:
            try:
                message_str = str(message, 'utf-8')
            except:
                message_str = repr(message)
        message_str = message_str.strip()
        logger.log(level, message_str)
        dq.append((self.now_local, message_str))


    def get_logs(self):
        return list(self.logs_dq)[::]


    def get_stdout(self):
        return list(self.stdout_dq)[::]


    def get_stderr(self):
        return list(self.stderr_dq)[::]


    def create(self, app):
        self.app = app
        self.app['process'] = self
        self.proc = None
        self.is_stopped = False
        self.coro_watch_restart = None
        self.coro_read_stdout = None
        self.coro_read_stderr = None


    async def start(self):
        self.is_stopped = False
        if self.proc is None:
            self.coro = asyncio.create_subprocess_exec(
                *self.app.cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            self.is_stopped = False
            self.proc = await asyncio.ensure_future(self.coro)

            if self.coro_read_stdout is not None:
                self.coro_read_stdout.cancel()
            self.coro_read_stdout = asyncio.ensure_future(self.read_stdout())
            if self.coro_read_stderr is not None:
                self.coro_read_stderr.cancel()
            self.coro_read_stderr = asyncio.ensure_future(self.read_stderr())

            if self.coro_watch_restart is not None:
                self.coro_watch_restart.cancel()
            self.coro_watch_restart = asyncio.ensure_future(self.watch_and_restart())

            return 'Process started'
        else:
            return 'Process not started, already running'


    async def startup(self, app):
        self.create(app)
        await self.start()


    async def shutdown(self, _):
        await asyncio.shield(self.stop())


    async def stop(self):
        self.is_stopped = True
        if self.proc is None:
            return 'Process already stopped'
        try:
            self.proc.terminate()
        except ProcessLookupError:
            self.logger('logs', 'warning', 'Process is already dead when attempting to stop')
        try:
            await asyncio.wait_for(self.proc.wait(), self.term_timeout)
            self.proc = None
            return 'Process stopped'
        except:
            self.proc.kill()
            self.proc = None
            return 'Process killed'
        await asyncio.sleep(1)


    async def restart(self):
        self.logger('logs', 'error', 'Restarting in %d seconds' % self.restart_delay)
        await asyncio.sleep(self.restart_delay)
        await self.stop()
        await self.start()


    async def watch_and_restart(self):
        while not self.is_stopped:
            if self.proc.returncode is not None:
                self.logger('logs', 'error', 'Process died')
                await self.restart()
            await asyncio.sleep(0.1)


    async def read_stdout(self):
        while not self.is_stopped:
            line = await self.proc.stdout.readline()
            if line == b'':
                break
            self.logger('stdout', 'info', line)
        self.coro_read_stdout = None


    async def read_stderr(self):
        while not self.is_stopped:
            line = await self.proc.stderr.readline()
            if b'java.lang.OutOfMemoryError' in line:
                self.logger('logs', 'error', 'java.lang.OutOfMemoryError found in stderr, restarting')
                asyncio.ensure_future(self.restart())
            if b'java.lang.NullPointerException' in line:
                self.logger('logs', 'error', 'java.lang.NullPointerException found in stderr, restarting')
                asyncio.ensure_future(self.restart())
            #if b'Failed to load module' in line:
            #    self.logger('logs', 'error', 'Failed to load module found in stdout, restarting')
            #    asyncio.ensure_future(self.restart())
            if line == b'':
                break
            self.logger('stderr', 'info', line)
        self.coro_read_stderr = None


    def status(self):
        if self.proc is None:
            return 'Process is not running'
        else:
            return 'Process is running as %s' % self.proc.pid


    def logs(self):
        return {
            'stdout': self.get_stdout(),
            'stderr': self.get_stderr(),
            'logs': self.get_logs(),
        }


def create_app(cmd):

    app = aiohttp.web.Application()
    app.router.add_route('GET', '/', handler_status)
    app.router.add_route('GET', '/status', handler_status)
    app.router.add_route('GET', '/start', handler_start)
    app.router.add_route('GET', '/stop', handler_stop)
    app.router.add_route('GET', '/logs', handler_logs)
    app.cmd = cmd

    process = MyProcess(restart_delay=30)

    app.on_startup.append(process.startup)
    app.on_shutdown.append(process.shutdown)

    return app


if __name__ == "__main__":

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)-8s [%(name)-8s] %(message)s')

    if os.name == 'nt':
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)

    BIND = '0.0.0.0'
    PORT = 8080
    #EXEC = ['/usr/bin/nicotine']
    #EXEC = ['/tmp/tototo']
    os.chdir('Z:/javaApp')
    EXEC = ['C:/java/bin/java', '-Xmx1024m', '-jar', 'Z:/javaApp/javaApp.jar']


    APP = create_app(EXEC)
    aiohttp.web.run_app(APP, host=BIND, port=PORT)
