"""
apps/api/limiter.py
--------------------
The single shared rate limiter.

Previously main.py and routers/reading.py each constructed their own
``Limiter``. Only main.py's instance was registered as ``app.state.limiter``
and wired to the exception handler, so the two disagreed about which limiter
was authoritative. Constructing it once here removes the ambiguity — every
module decorates against the same instance that the app has registered.

Endpoints decorated with ``@limiter.limit(...)`` MUST declare a
``request: Request`` parameter; slowapi reads the client address from it.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
