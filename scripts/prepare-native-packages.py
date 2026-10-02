"""作品说明：准备验收使用的固定版本本机依赖，避免跟随最新版产生环境漂移。"""
import hashlib,json,sys,urllib.request,zipfile,zlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
    folder=ROOT/'.local_runtime/native';folder.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((ROOT/'scripts/native-packages.json').read_text(encoding='utf-8'));records=[]
    for item in manifest['packages']:
        target=folder/item['url'].rsplit('/',1)[-1]
        if not target.exists():
            partial=target.with_suffix(target.suffix+'.partial')
            request=urllib.request.Request(item['url'],headers={'User-Agent':'FinSight-native-setup'})
            with urllib.request.urlopen(request,timeout=60) as response,partial.open('wb') as output:
                while block:=response.read(1024*1024):output.write(block)
            if hashlib.sha256(partial.read_bytes()).hexdigest()!=item['sha256']:
                raise RuntimeError('Native package checksum mismatch; no extraction performed')
            partial.replace(target)
        if hashlib.sha256(target.read_bytes()).hexdigest()!=item['sha256']:
            raise RuntimeError('Existing native package checksum mismatch')
        destination=folder/item['name'];destination.mkdir(exist_ok=True)
        if not (destination/'.finsight-package.json').exists():
            with zipfile.ZipFile(target) as archive:
                if any(not (destination/name).resolve().is_relative_to(destination.resolve()) for name in archive.namelist()):
                    raise RuntimeError('Archive member escapes its native package directory')
                # 作品说明：已准备的软件包可能正在运行 Erlang；写入二进制前先与指定 ZIP 核对身份。
                complete=all(info.is_dir() or (destination/info.filename).is_file() and
                    (destination/info.filename).stat().st_size==info.file_size and
                    zlib.crc32((destination/info.filename).read_bytes())==info.CRC for info in archive.infolist())
                if not complete:
                    if any(destination.iterdir()):
                        raise RuntimeError('Existing native package is incomplete or differs; no running binary was overwritten')
                    archive.extractall(destination)
            (destination/'.finsight-package.json').write_text(json.dumps(item,indent=2),encoding='utf-8')
        records.append(dict(name=item['name'],version=item['version'],sha256=item['sha256'],ready=True))
    (folder/'package-readiness.json').write_text(json.dumps(records,indent=2),encoding='utf-8');print(json.dumps(records))
    return 0

if __name__=='__main__':sys.exit(main())
