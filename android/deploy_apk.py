import py_compile
import paramiko
import urllib.request

py_compile.compile(r"../server/app.py", doraise=True)
print("Local syntax check OK")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("192.168.0.x", username="example", password="example_password", timeout=10)

sftp = ssh.open_sftp()
sftp.put(r"./MeshMonitor.apk", "/home/example/mesh-monitor/MeshMonitor.apk")
sftp.put(r"../server/icon-192.png", "/home/example/mesh-monitor/icon-192.png")
sftp.put(r"../server/icon-512.png", "/home/example/mesh-monitor/icon-512.png")
sftp.put(r"../server/app.py", "/home/example/mesh-monitor/app.py")
sftp.close()
print("Files uploaded via SFTP")

stdin, stdout, stderr = ssh.exec_command("sudo -S systemctl restart mesh-monitor.service && sleep 8 && systemctl is-active mesh-monitor.service")
stdin.write("example_password\n")
stdin.flush()
print("Service status:", stdout.read().decode().strip(), stderr.read().decode().strip())
ssh.close()

for path in ["/", "/api/notify", "/MeshMonitor.apk"]:
    req = urllib.request.urlopen(f"http://192.168.0.x:8080{path}", timeout=5)
    data = req.read()
    print(f"GET {path} -> HTTP {req.status}, {len(data)} bytes, Content-Type: {req.headers.get('Content-Type')}")
