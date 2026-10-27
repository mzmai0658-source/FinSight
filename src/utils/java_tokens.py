"""长时间上传及问答验收使用同一双令牌协议；凭据仅保存在内存。"""
from __future__ import annotations


class JavaSessionExpired(ValueError):
    pass


class RotatingJavaTokens:
    def __init__(self, http, base, pair):
        self._http,self._base=http,base.rstrip('/')
        self._install(pair)

    def _install(self,pair):
        access=pair.get('accessToken') if isinstance(pair,dict) else None
        if not isinstance(access,str) or not access.strip():
            raise JavaSessionExpired('Java login did not return a valid access credential')
        self._http.headers['Authorization']='Bearer '+access
        self._refresh=pair.get('refreshToken')

    def renew(self):
        if not isinstance(self._refresh,str) or not self._refresh.strip():
            raise JavaSessionExpired('Java login expired; rerun the same command to recover saved progress')
        response=None
        try:
            response=self._http.request('POST',self._base+'/api/auth/refresh',
                json=dict(refreshToken=self._refresh),headers={'Authorization':None},timeout=(10,15))
            if response.status_code!=200:raise ValueError()
            value=response.json()
            pair=value.get('data') if isinstance(value,dict) and value.get('code')==0 else None
            if not isinstance(pair,dict) or not isinstance(pair.get('refreshToken'),str) or not pair['refreshToken'].strip():
                raise ValueError()
            self._install(pair)
        except Exception:
            raise JavaSessionExpired('Java credential renewal failed; rerun the same command to recover saved progress') from None
        finally:
            if response is not None:response.close()
