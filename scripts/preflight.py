"""作品说明：探测依赖的实际协议，不输出连接凭据。"""
from __future__ import annotations
import argparse,json,os,socket,struct,sys,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
def run():
    from sqlalchemy import create_engine,text
    from config.db_config import get_db_config
    result=[]
    def check(name,fn):
        try: detail=fn();result.append(dict(name=name,ok=True,detail=detail))
        except Exception as exc:
            hints={'mysql':'检查MySQL连接、已验收发布的数据版本和独立只读账号SELECT权限；先完成demo-seed。',
                   'ollama':'启动本机Ollama并准备所配置模型；核对LLM_*与OLLAMA_*优先级和连接地址。',
                   'redis':'检查Redis地址、密码及REDIS_DATABASE；需要服务端支持所选数据库号。',
                   'rabbitmq':'检查AMQP端口、账号密码和RABBITMQ_VHOST；指定vhost须提前创建并授权。'}
            result.append(dict(name=name,ok=False,detail=type(exc).__name__,hint=hints[name]))
    def database():
        from src.agent.v3.repository import CanonicalRepository
        from src.etl.release_profiles import check_report_coverage
        from config.db_config import get_readonly_db_config
        engine=create_engine(get_readonly_db_config().connection_string)
        repository=CanonicalRepository(engine)
        profile=check_report_coverage(repository.manifest,repository.report_catalog())
        return f'MySQL read-only accepted {profile.kind} version: {profile.reports} reports'
    def ollama():
        from src.agent.providers import resolve_chat_provider
        config=resolve_chat_provider()
        assert config.provider=='ollama', 'Public demo requires local Ollama'
        host=config.base_url.removesuffix('/v1').rstrip('/')
        payload=json.load(urllib.request.urlopen(host+'/api/tags',timeout=5))
        assert any(m['name']==config.model for m in payload['models']), 'Configured Ollama model missing'
        return 'Native Ollama API, local models present'
    def redis():
        with socket.create_connection((os.getenv('REDIS_HOST','127.0.0.1'),int(os.getenv('REDIS_PORT',6379))),timeout=5) as client:
            stream=client.makefile('rb')
            def command(*args):
                encoded=[arg.encode() for arg in args]
                client.sendall(b'*'+str(len(args)).encode()+b'\r\n'+b''.join(b'$'+str(len(arg)).encode()+b'\r\n'+arg+b'\r\n' for arg in encoded))
                return stream.readline()
            if os.getenv('REDIS_PASSWORD'): assert command('AUTH',os.environ['REDIS_PASSWORD'])==b'+OK\r\n'
            assert command('SELECT',os.getenv('REDIS_DATABASE','0'))==b'+OK\r\n'
            assert command('PING')==b'+PONG\r\n'
        return 'Authenticated Redis PING'
    def rabbit():
        def short(value):
            value=value.encode();return bytes([len(value)])+value
        def long(value):return struct.pack('!I',len(value))+value
        with socket.create_connection((os.getenv('RABBITMQ_HOST','127.0.0.1'),int(os.getenv('RABBITMQ_PORT',5672))),timeout=5) as connection:
            stream=connection.makefile('rb')
            def read_exact(length):
                data=stream.read(length);assert len(data)==length;return data
            def receive(expected):
                kind,channel,length=struct.unpack('!BHI',read_exact(7))
                payload=read_exact(length);assert read_exact(1)==b'\xce'
                assert kind==1 and struct.unpack('!HH',payload[:4])==expected
                return payload[4:]
            def send(channel,cls,method,payload=b''):
                body=struct.pack('!HH',cls,method)+payload
                connection.sendall(struct.pack('!BHI',1,channel,len(body))+body+b'\xce')
            connection.sendall(b'AMQP\x00\x00\x09\x01');receive((10,10))
            credentials=b'\x00'+os.getenv('RABBITMQ_USERNAME','guest').encode()+b'\x00'+os.getenv('RABBITMQ_PASSWORD','guest').encode()
            send(0,10,11,b'\x00'*4+short('PLAIN')+long(credentials)+short('en_US'))
            channel_max,frame_max,heartbeat=struct.unpack('!HIH',receive((10,30)))
            send(0,10,31,struct.pack('!HIH',channel_max,frame_max,0))
            send(0,10,40,short(os.getenv('RABBITMQ_VHOST','/'))+short('')+b'\x00');receive((10,41))
            send(1,20,10,short(''));receive((20,11))
            send(1,50,10,b'\x00\x00'+short('')+b'\x0c'+b'\x00'*4);receive((50,11))
            send(0,10,50,struct.pack('!H',200)+short('preflight finished')+b'\x00'*4);receive((10,51))
        return 'Authenticated AMQP handshake and exclusive queue declaration'
    for name,fn in [('mysql',database),('ollama',ollama),('redis',redis),('rabbitmq',rabbit)]: check(name,fn)
    return result
def main():
    p=argparse.ArgumentParser();p.add_argument('--env',default='.env.integration');p.add_argument('--output',default='data/runtime/v3/preflight.json');args=p.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT/args.env,override=True)
    rows=run();target=ROOT/args.output;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(rows,indent=2),encoding='utf-8')
    print(json.dumps(rows));return 0 if all(r['ok'] for r in rows) else 1
if __name__=='__main__':sys.exit(main())
