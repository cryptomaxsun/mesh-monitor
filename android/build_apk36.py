import os
import shutil
import subprocess
import zipfile

base = r"./android-build"
jdk = os.path.join(base, "jdk-17.0.21+9")
bt = os.path.join(base, "android-16")
platform = os.path.join(base, "android-36", "android.jar")
app_dir = os.path.join(base, "app")
out_dir = os.path.join(base, "out")
final_apk = r"./MeshMonitor.apk"

env = os.environ.copy()
env["JAVA_HOME"] = jdk
env["PATH"] = os.path.join(jdk, "bin") + os.pathsep + env.get("PATH", "")

# Generate adaptive icon and XML resources first
subprocess.run(["python", r"./gen_adaptive_res.py"], check=True)

# Also copy fallback ic_launcher.png into mipmap-xhdpi for Android 5-7
mipmap_xhdpi = os.path.join(app_dir, "res", "mipmap-xhdpi")
os.makedirs(mipmap_xhdpi, exist_ok=True)
shutil.copyfile(os.path.join(app_dir, "res", "drawable", "ic_launcher.png"), os.path.join(mipmap_xhdpi, "ic_launcher.png"))

# Clean and recreate out dirs
for sub in ["gen", "obj", "dex"]:
    p = os.path.join(out_dir, sub)
    if os.path.exists(p):
        shutil.rmtree(p)
    os.makedirs(p, exist_ok=True)

aapt2 = os.path.join(bt, "aapt2.exe")
d8 = os.path.join(bt, "d8.bat")
zipalign = os.path.join(bt, "zipalign.exe")
apksigner = os.path.join(bt, "apksigner.bat")
javac = os.path.join(jdk, "bin", "javac.exe")

res_zip = os.path.join(out_dir, "res.zip")
unsigned_apk = os.path.join(out_dir, "unsigned.apk")
unaligned_apk = os.path.join(out_dir, "unaligned.apk")
aligned_apk = os.path.join(out_dir, "aligned.apk")
keystore = os.path.join(out_dir, "release.keystore")

print("1. Compiling resources with aapt2 (Build-Tools 36.1.0)...")
subprocess.run([aapt2, "compile", "--dir", os.path.join(app_dir, "res"), "-o", res_zip], check=True, env=env)

print("2. Linking resources with aapt2 against Android 16 (API 36)...")
subprocess.run([
    aapt2, "link",
    "-I", platform,
    "--manifest", os.path.join(app_dir, "AndroidManifest.xml"),
    "--java", os.path.join(out_dir, "gen"),
    "--min-sdk-version", "21",
    "--target-sdk-version", "36",
    "-o", unsigned_apk,
    res_zip
], check=True, env=env)

print("3. Compiling Java sources with javac...")
src_pkg = os.path.join(app_dir, "src", "com", "maksim8", "meshmonitor")
java_files = [os.path.join(src_pkg, f) for f in os.listdir(src_pkg) if f.endswith(".java")] + [
    os.path.join(out_dir, "gen", "com", "maksim8", "meshmonitor", "R.java"),
]
subprocess.run([
    javac,
    "-source", "1.8", "-target", "1.8",
    "-encoding", "UTF-8",
    "-bootclasspath", platform,
    "-classpath", platform,
    "-d", os.path.join(out_dir, "obj"),
    *java_files
], check=True, env=env)

print("4. Dexing with d8 (min-api 21)...")
class_dir = os.path.join(out_dir, "obj", "com", "maksim8", "meshmonitor")
class_files = [os.path.join(class_dir, f) for f in os.listdir(class_dir) if f.endswith(".class")]
subprocess.run([
    d8,
    "--release",
    "--min-api", "21",
    "--lib", platform,
    "--output", os.path.join(out_dir, "dex"),
    *class_files
], check=True, env=env)

print("5. Packaging classes.dex into APK...")
shutil.copyfile(unsigned_apk, unaligned_apk)
with zipfile.ZipFile(unaligned_apk, "a") as z:
    z.write(os.path.join(out_dir, "dex", "classes.dex"), "classes.dex", compress_type=zipfile.ZIP_DEFLATED)

print("6. Aligning APK with zipalign -p -f -v 4 (16 KB page size compatible)...")
if os.path.exists(aligned_apk):
    os.remove(aligned_apk)
subprocess.run([zipalign, "-p", "-f", "4", unaligned_apk, aligned_apk], check=True, env=env)

print("7. Signing APK with v1, v2, v3 signatures...")
keytool = os.path.join(jdk, "bin", "keytool.exe")
if not os.path.exists(keystore):
  subprocess.run([
    keytool, "-genkeypair", "-v",
    "-keystore", keystore,
    "-alias", "meshkey",
    "-keyalg", "RSA",
    "-keysize", "2048",
    "-validity", "10000",
    "-storepass", "android123",
    "-keypass", "android123",
    "-dname", "CN=Mesh Monitor, OU=Mesh, O=Example, L=City, ST=Region, C=UA"
], check=True, env=env)
subprocess.run([
    apksigner, "sign",
    "--ks", keystore,
    "--ks-key-alias", "meshkey",
    "--ks-pass", "pass:android123",
    "--key-pass", "pass:android123",
    "--v1-signing-enabled", "true",
    "--v2-signing-enabled", "true",
    "--v3-signing-enabled", "true",
    "--v4-signing-enabled", "false",
    "--out", final_apk,
    aligned_apk
], check=True, env=env)

print("8. Verifying APK signature and SDK compatibility...")
res = subprocess.run([apksigner, "verify", "-v", "--print-certs", final_apk], check=True, capture_output=True, text=True, env=env)
print(res.stdout)

badging = subprocess.run([aapt2, "dump", "badging", final_apk], check=True, capture_output=True, text=True, env=env)
for line in badging.stdout.splitlines():
    if any(k in line for k in ("package:", "sdkVersion:", "targetSdkVersion:", "application:")):
        print("BADGING:", line)
