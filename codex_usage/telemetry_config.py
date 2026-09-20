"""Manage only Token Lens's local exporter block; never replace another exporter."""
import json
import re
import secrets
import time
from pathlib import Path
from .lifecycle import atomic_write

START='# BEGIN TOKEN LENS LOCAL TELEMETRY'
END='# END TOKEN LENS LOCAL TELEMETRY'


def configure(home, data, port, enabled):
    home,data=Path(home),Path(data)
    data.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=home/'config.toml';metadata=data/'telemetry-config.json';token_path=data/'telemetry-token'
    original=path.read_text() if path.exists() else ''
    saved=json.loads(metadata.read_text()) if metadata.exists() else {}
    prior=saved.get('block')
    if START in original and (not prior or original.count(prior)!=1):
        raise ValueError('本工具的遥测配置已被修改，已保留；请先核对 config.toml')
    remaining=original.replace(prior,'',1) if prior and prior in original else original
    if enabled:
        if re.search(r"^\s*(?:\[\s*[\"']?otel[\"']?\s*(?:\]|\.)|[\"']?otel[\"']?\s*[.=])",remaining,re.M):
            raise ValueError('已有 OTel 配置，未覆盖；请将现有采集器转发至本地，或手动整合')
        token=token_path.read_text().strip() if token_path.exists() else secrets.token_hex(32)
        block='\n'+START+'\n[otel]\nlog_user_prompt = true\nexporter = { otlp-http = { endpoint = "http://127.0.0.1:%d/v1/logs", protocol = "json", headers = { "X-Token-Lens-Key" = "%s" } } }\n%s\n' % (port,token,END)
        updated=remaining+block
    else:
        block='';updated=remaining
    if updated!=original:
        if path.exists():
            backup=home/('config.toml.token-lens-backup-'+str(time.time_ns()))
            atomic_write(backup,original.encode())
        atomic_write(path,updated.encode())
    if enabled:atomic_write(token_path,token.encode())
    else:token_path.unlink(missing_ok=True)
    state=dict(enabled=bool(enabled),port=port,configured_at=time.time(),restart_required=True,block=block)
    atomic_write(metadata,(json.dumps(state)+'\n').encode())
    from .store import connect
    con=connect(data)
    try:
        with con:
            con.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('telemetry_config',json.dumps({k:v for k,v in state.items() if k!='block'})))
    finally:con.close()
    return {k:v for k,v in state.items() if k!='block'}
