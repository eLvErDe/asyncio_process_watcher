# Usage

Supervisor script using Python3 asyncio.

Set proper EXEC array of the target command and it will start it and watch process is alive, watch its stderr and stdout (restart actions can be plugged here, see included ones).

An API is available on port 8080, with / and /status providing information on the running process, /logs provide stdout/stderr/internal logs as JSON and /start /stop provide helper to manually handle the supervised processus.

Successfully tested with a graphical application on Windows, so it's probably going to run ANYWHERE. 

As it's asyncio based it sould be fairly easy to add more advanced process aliveness check, like a background coroutine doing HTTP/TCP calls to the actual service for instance.
