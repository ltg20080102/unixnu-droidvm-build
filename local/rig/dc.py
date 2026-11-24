import socket, struct, json, subprocess, uuid, sys
ADB = "/mnt/d/bootloaderbreak/极光の粗粮8EliteGen5设备一键解锁bl/adb.exe"
def adb(cmd):
    return subprocess.run([ADB,'-P','5038','shell','su -c',cmd],capture_output=True).stdout
port = int(adb("cat /data/data/cn.classfun.droidvm/run/droidvmd-port.txt").decode().strip())
token = adb("cat /data/data/cn.classfun.droidvm/run/droidvmd-token.txt").decode().strip()
subprocess.run([ADB,'-P','5038','forward','tcp:%d'%port,'tcp:%d'%port],capture_output=True)
s = socket.create_connection(("127.0.0.1",port),timeout=10); s.settimeout(30)
def send(obj):
    j = json.dumps(obj).encode(); s.sendall(struct.pack('<I',len(j)+1)+j+b'\0')
def recv():
    h=b""
    while len(h)<4: h+=s.recv(4-len(h))
    n=struct.unpack('<I',h)[0]; b=b""
    while len(b)<n: b+=s.recv(n-len(b))
    return json.loads(b.rstrip(b'\0'))
def call(command, **kw):
    req={"command":command,"type":"request","request_id":str(uuid.uuid4())}; req.update(kw)
    send(req); return recv()
