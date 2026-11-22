#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Alas 部署自检。放在 Alas 根目录下运行：

    python dev_tools/deploy_check.py

用来判断一份 Alas 目录能不能启动，以及「覆盖原版后起不来」卡在哪一环。
不需要联网，不会改动任何文件。
"""
import os
import sys
import json

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

results = []  # (status, name, detail, fix)


def ok(name, detail=""):
    results.append(("PASS", name, detail, ""))


def bad(name, detail, fix):
    results.append(("FAIL", name, detail, fix))


def warn(name, detail, fix=""):
    results.append(("WARN", name, detail, fix))


def check_python():
    v = sys.version_info
    name = "Python 版本"
    if (v.major, v.minor) == (3, 7):
        ok(name, "%d.%d.%d（与本项目一致）" % (v.major, v.minor, v.micro))
    elif v.major == 3 and v.minor >= 8:
        warn(name, "%d.%d.%d 高于项目锁定的 3.7.6" % (v.major, v.minor, v.micro),
             "依赖表（numpy==1.16.6 / mxnet==1.6.0）是 3.7 时代的，高版本可能装不上")
    else:
        bad(name, "%d.%d.%d 版本过低或为 Python 2" % (v.major, v.minor, v.micro),
            "请用本目录 toolkit/python.exe（3.7.6 64bit）运行")


def check_layout():
    # 核心文件
    for rel, desc in [
        ("alas.py", "主入口"),
        ("gui.py", "WebUI 入口"),
        ("requirements.txt", "依赖表"),
    ]:
        if os.path.isfile(os.path.join(ROOT, rel)):
            ok("文件存在: " + rel, desc)
        else:
            bad("文件缺失: " + rel, "整个目录可能没解压完整", "重新解压迁移包，不要手工挑文件")

    # 本版本的标志性文件
    for rel in [
        "module/webui/upstream_sync.py",
        "module/island/production.py",
        "module/island/utils.py",
        "dev_tools/island_bedtime_check.py",
    ]:
        if os.path.isfile(os.path.join(ROOT, rel)):
            ok("本版本特有: " + rel)
        else:
            bad("缺少本版本特有: " + rel,
                "这份目录是原版 Alas，不是带岛屿改进的版本",
                "用 alas-core.tar.gz 解压，不要拿原版文件去凑")

    # toolkit
    py = os.path.join(ROOT, "toolkit", "python.exe")
    if os.path.isfile(py):
        ok("toolkit/python.exe 存在")
    else:
        bad("toolkit/python.exe 不存在", "内嵌环境没有解压",
            "解压 alas-toolkit-win64.tar.gz 到本目录下，让 toolkit/ 直接是子目录")


def check_nested():
    """识别「解压多套了一层」这种最常见错误。"""
    inner = os.path.join(ROOT, "AzurLaneAutoScript")
    if os.path.isdir(inner):
        bad("目录嵌套",
            "在 %s 下又发现一个 AzurLaneAutoScript 子目录" % ROOT,
            "解压时会得到 AzurLaneAutoScript 文件夹，应该把它整个放到目标盘根下，"
            "而不是塞进已有的 AzurLaneAutoScript 里。把内层内容上移一层。")


def check_config():
    cfg = os.path.join(ROOT, "config", "alas.json")
    if not os.path.isfile(cfg):
        bad("config/alas.json 不存在", "用户配置丢失", "从原版 Alas 拷贝过来，或从迁移包里取")
        return
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        bad("config/alas.json 解析失败", str(e), "文件可能被覆盖成半截，从备份恢复")
        return

    emu = (data.get("Alas") or {}).get("Emulator") or {}
    info = (data.get("Alas") or {}).get("EmulatorInfo") or {}
    if not emu:
        bad("config/alas.json 缺少 Alas.Emulator 段", "配置被覆盖成了别的版本",
            "从迁移包或备份里恢复这一段")
    else:
        serial = emu.get("Serial", "")
        shot = emu.get("ScreenshotMethod", "")
        ok("Alas.Emulator", "Serial=%s ScreenshotMethod=%s" % (serial, shot))
        if shot == "nemu_ipc":
            path = info.get("path", "")
            if not path or not os.path.isfile(path):
                warn("nemu_ipc 的模拟器路径无效",
                     "EmulatorInfo.path=%s 在这台机器上不存在" % (path or "(空)"),
                     "改成这台机器上 MuMuNxMain.exe 的实际路径，"
                     "或者把 ScreenshotMethod 改成 \"adb\" 先跑通")
            else:
                ok("nemu_ipc 模拟器路径有效", path)
        pkg = emu.get("PackageName", "")
        if pkg and pkg != "com.bilibili.azurlane":
            warn("游戏包名不是 B 服", "PackageName=%s" % pkg, "确认这台机器装的是哪个渠道服")

    # deploy.yaml
    dep = os.path.join(ROOT, "config", "deploy.yaml")
    if not os.path.isfile(dep):
        bad("config/deploy.yaml 不存在", "", "从迁移包或原版恢复")
        return
    try:
        import yaml
    except Exception:
        warn("没有 pyyaml，跳过 deploy.yaml 校验", "")
        return
    try:
        with open(dep, "r", encoding="utf-8") as f:
            d = yaml.safe_load(f)
    except Exception as e:
        bad("config/deploy.yaml 解析失败", str(e), "重新解压，别手工编辑缩进")
        return
    git = (d.get("Deploy") or {}).get("Git") or {}
    auto = git.get("AutoUpdate")
    if auto is True:
        warn("Deploy.Git.AutoUpdate = true",
             "启动时会 git reset --hard origin/master，把本地改动全部冲掉",
             "本版本必须改成 false，否则改动会被静默还原")
    elif auto is False:
        ok("Deploy.Git.AutoUpdate = false", "本地改动不会被 reset")


def _adb_path():
    bundled = os.path.join(
        ROOT, "toolkit", "Lib", "site-packages", "adbutils", "binaries", "adb.exe")
    if os.path.isfile(bundled):
        return bundled
    for p in os.environ.get("PATH", "").split(os.pathsep):
        cand = os.path.join(p, "adb.exe")
        if os.path.isfile(cand):
            return cand
    return None


def _adb_devices(adb, timeout=15):
    """Return [(serial, status)], or None when adb cannot be run."""
    import subprocess

    def run(*args):
        try:
            out = subprocess.run([adb] + list(args), stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, timeout=timeout)
            return out.stdout.decode("utf-8", "replace")
        except Exception:
            return ""

    run("start-server")
    text = run("devices")
    devices = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            devices.append((parts[0], parts[1]))
    return devices


def check_device():
    """配置里的 Serial 是否真能连上，auto 在多设备时是否靠不住。"""
    cfg = os.path.join(ROOT, "config", "alas.json")
    if not os.path.isfile(cfg):
        return
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return

    emu = (data.get("Alas") or {}).get("Emulator") or {}
    serial = emu.get("Serial", "")
    if not serial:
        return

    adb = _adb_path()
    if not adb:
        warn("找不到 adb，跳过设备检查", "", "toolkit 未解压时属正常")
        return

    devices = _adb_devices(adb)
    if devices is None:
        warn("adb 无法执行，跳过设备检查")
        return

    names = ", ".join("%s(%s)" % (s, st) for s, st in devices) or "(无)"

    if serial == "auto":
        if len(devices) == 1:
            ok("Alas.Emulator.Serial = auto", "当前只有 1 台设备，自动识别可用")
        else:
            warn("%d 台设备时 auto 无法自动选择" % len(devices),
                 "当前可见: %s" % names,
                 "把 Serial 改成上面某一个具体值（形如 127.0.0.1:16384），"
                 "MuMu 会同时暴露 emulator-5554，auto 因此失效")
        return

    # 127.0.0.1:PORT 形式，先像 Alas 一样 connect 一次再看
    if serial.startswith("127.0.0.1:") or serial.startswith("localhost:"):
        if not any(s == serial and st == "device" for s, st in devices):
            import subprocess
            try:
                subprocess.run([adb, "connect", serial], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=15)
            except Exception:
                pass
            devices = _adb_devices(adb)
            names = ", ".join("%s(%s)" % (s, st) for s, st in devices) or "(无)"

    hit = [st for s, st in devices if s == serial]
    if not hit:
        warn("配置的 Serial 当前没连上",
             "Serial=%s，可见设备: %s" % (serial, names),
             "Alas 启动时会用 MuMuManager 自己拉起配置的那个实例，所以先启动 Alas 也能连上；"
             "若一直连不上，说明 MuMu 多开里没有对应实例（端口规则：首个 16384，之后每个 +32），"
             "或改用 adb devices 里的具体值")
    elif hit[0] != "device":
        warn("配置的 Serial 状态异常",
             "Serial=%s 状态 %s" % (serial, hit[0]),
             "设备离线多半是模拟器没启动完，等它起来再跑一次")
    else:
        ok("Alas.Emulator.Serial 可连接", "%s (device)" % serial)


def check_git():
    gitdir = os.path.join(ROOT, ".git")
    if not os.path.isdir(gitdir):
        warn(".git 不存在", "上游自动合并与更新按钮会失效（仍可正常跑任务）",
             "如果不需要跟上游，可忽略")
        return
    ok(".git 存在")
    head = os.path.join(gitdir, "HEAD")
    try:
        with open(head, "r", encoding="utf-8") as f:
            tip = f.read().strip()
    except Exception:
        tip = ""
    ok(".git/HEAD", tip)


def check_imports():
    """只在用本目录 python 跑时有意义。"""
    sys.path.insert(0, ROOT)
    for mod in ["module.config.config_generated", "module.island.utils", "module.island.production"]:
        try:
            __import__(mod)
            ok("可导入 " + mod)
        except Exception as e:
            bad("导入失败 " + mod, "%s: %s" % (type(e).__name__, e),
                "用本目录 toolkit/python.exe 运行本脚本，并确认 requirements 已安装")


def main():
    print("Alas 部署自检")
    print("目录: %s" % ROOT)
    print("Python: %s" % sys.version.replace("\n", " "))
    print("=" * 72)

    check_python()
    check_nested()
    check_layout()
    check_config()
    check_device()
    check_git()
    if os.path.basename(sys.executable).lower().startswith("python"):
        check_imports()

    print()
    for status, name, detail, fix in results:
        mark = {"PASS": "[ OK ]", "FAIL": "[FAIL]", "WARN": "[WARN]"}[status]
        line = "%s %s" % (mark, name)
        if detail:
            line += "  ->  " + detail
        print(line)
        if fix and status != "PASS":
            print("        修复: " + fix)

    print()
    n_fail = sum(1 for r in results if r[0] == "FAIL")
    n_warn = sum(1 for r in results if r[0] == "WARN")
    print("结果: %d 通过, %d 警告, %d 失败" % (len(results) - n_fail - n_warn, n_warn, n_fail))
    if n_fail:
        print("存在致命问题，修完再启动。")
        sys.exit(1)
    print("可以启动。若仍失败，把启动时的完整报错贴出来。")
    sys.exit(0)


if __name__ == "__main__":
    main()
