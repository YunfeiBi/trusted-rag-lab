from uuid import uuid4

from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse


class UploadBodyLimit:
    """Bound the entire multipart body before the parser can spool it to disk."""
    def __init__(self, app, max_bytes):
        self.app, self.max_bytes = app, max_bytes+1024*1024

    async def __call__(self, scope, receive, send):
        if scope['type']!='http' or scope.get('method')!='POST' or not scope.get('path','').startswith('/api/documents'):
            return await self.app(scope,receive,send)
        headers = dict(scope.get('headers',[]))
        try:
            length = int(headers.get(b'content-length',b'0'))
        except ValueError:
            length = 0
        if length > self.max_bytes:
            trace = uuid4().hex
            response = JSONResponse({'error':{'code':'UPLOAD_TOO_LARGE','message':'上传请求超过当前大小上限。'},'trace_id':trace},status_code=413,headers={'X-Request-ID':trace,'Cache-Control':'no-store'})
            return await response(scope,receive,send)
        size = 0

        async def limited_receive():
            nonlocal size
            event = await receive()
            size += len(event.get('body',b''))
            if size > self.max_bytes:
                scope.setdefault('state',{})['upload_too_large'] = True
                # Multipart parser catches this type and closes spooled files.
                raise MultiPartException('上传请求超过当前大小上限。')
            return event

        await self.app(scope,limited_receive,send)
