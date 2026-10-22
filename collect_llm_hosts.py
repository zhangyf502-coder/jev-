#!/usr/bin/env python3
"""Collect Linux LLM host inventory over SSH and write Excel-friendly CSV files.

Only the Python standard library and the system OpenSSH client are required on
the collecting host. Remote hosts need a POSIX shell and their normal vendor
query commands; the script never installs an agent on them.
"""

import argparse
import csv
import io
import re
import shlex
import subprocess
import sys
from collections import namedtuple
from datetime import datetime
from pathlib import Path


Section = namedtuple("Section", "output returncode")

REPORT_FIELDS = {
    "servers": [
        "采集时间", "目标地址", "主机名", "系统", "内核", "架构", "CPU型号",
        "CPU逻辑核数", "CPU插槽数", "NUMA节点数", "内存总量_GiB", "内存可用_GiB",
        "IPv4摘要", "网络链路摘要", "加速卡厂商", "加速卡数量",
        "python3路径", "python3版本", "python路径", "python版本", "Docker_CLI版本",
        "CUDA驱动支持版本", "CUDA_Toolkit版本", "CANN版本", "采集状态",
    ],
    "accelerators": [
        "目标地址", "厂商", "设备编号", "稳定设备标识", "PCI地址", "型号",
        "设备内存类型", "设备内存总量_源值", "设备内存已用_源值",
        "设备内存可用_源值", "源单位", "利用率_百分比", "健康状态",
        "驱动版本", "固件版本", "分区状态", "采集状态",
    ],
    "storage": [
        "目标地址", "挂载点", "文件系统类型", "总量_GiB", "可用_GiB",
        "模型目录是否存在", "是否承载模型目录",
    ],
    "topology": [
        "目标地址", "设备A", "设备B", "连接类型", "查询状态", "原始摘要",
    ],
    "issues": [
        "目标地址", "阶段", "级别", "问题代码", "说明", "建议",
    ],
}


def validate_target(value):
    """Allow a literal SSH user/host, never command text or an SSH option."""
    pattern = r"(?:[A-Za-z_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9.-]*"
    if not re.fullmatch(pattern, value or ""):
        raise ValueError("无效 SSH 目标：{}".format(value))
    return value


def validate_model_dir(value):
    if value and (not value.startswith("/") or any(char in value for char in "\r\n\x00")):
        raise ValueError("--model-dir 必须是目标 Linux 服务器上的绝对目录")
    return value


def parse_sections(raw):
    """Parse framed outputs from the remote shell without losing exit codes."""
    result = {}
    current = None
    lines = []
    for line in raw.splitlines():
        start = re.fullmatch(r"__LLM_BEGIN__([A-Za-z0-9_]+)", line)
        end = re.fullmatch(r"__LLM_END__([A-Za-z0-9_]+):([0-9]+)", line)
        if start:
            current = start.group(1)
            lines = []
        elif end and current == end.group(1):
            result[current] = Section("\n".join(lines).strip(), int(end.group(2)))
            current = None
            lines = []
        elif current is not None:
            lines.append(line)
    return result


def parse_nvidia_csv(raw, target):
    """Convert nvidia-smi's documented CSV query into one row per GPU."""
    devices = []
    for values in csv.reader(io.StringIO(raw)):
        if not values or all(not item.strip() for item in values):
            continue
        if len(values) != 9:
            raise ValueError("NVIDIA CSV 列数不是 9")
        index, uuid, name, total, used, free, utilization, driver, bus = (
            item.strip() for item in values
        )
        if not index.isdigit():
            raise ValueError("NVIDIA 设备编号不可解析")
        devices.append({
            "目标地址": target, "厂商": "NVIDIA", "设备编号": index,
            "稳定设备标识": uuid, "PCI地址": bus, "型号": name,
            "设备内存类型": "FB显存", "设备内存总量_源值": total,
            "设备内存已用_源值": used, "设备内存可用_源值": free,
            "源单位": "MiB", "利用率_百分比": utilization,
            "健康状态": "未提供", "驱动版本": driver,
            "固件版本": "不适用", "分区状态": "未检测", "采集状态": "成功",
        })
    return devices


def parse_ascend_info(raw, target):
    """Read the common two-line npu-smi device table, including 310 variants."""
    devices = []
    pending = None
    for line in raw.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        first = re.fullmatch(r"(\d+)\s+(.+)", cells[0])
        # Health labels vary across npu-smi versions. The second line has a
        # PCI bus address here, which lets us recognize the summary row without
        # assuming a fixed set of health labels.
        bus_id = re.fullmatch(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]", cells[1])
        if first and cells[1] and not bus_id:
            pending = {
                "目标地址": target, "厂商": "Ascend", "设备编号": first.group(1),
                "稳定设备标识": "", "PCI地址": "", "型号": first.group(2).strip(),
                "设备内存类型": "未提供", "设备内存总量_源值": "",
                "设备内存已用_源值": "", "设备内存可用_源值": "",
                "源单位": "MB", "利用率_百分比": "", "健康状态": cells[1],
                "驱动版本": "", "固件版本": "", "分区状态": "未检测",
                "采集状态": "部分成功",
            }
            devices.append(pending)
            continue
        if pending and re.fullmatch(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]", cells[1]):
            pending["PCI地址"] = cells[1]
            pending["稳定设备标识"] = cells[1]
            # npu-smi often places AICore, Memory and HBM in one table cell.
            usage_pairs = re.findall(r"(\d+)\s*/\s*(\d+)", " ".join(cells[2:]))
            if len(usage_pairs) >= 2 and "HBM-Usage" in raw:
                used, total = map(int, usage_pairs[-1])
                pending["设备内存类型"] = "HBM"
            elif usage_pairs:
                used, total = map(int, usage_pairs[0])
                pending["设备内存类型"] = "设备内存"
            else:
                used = total = None
            pair = (str(total), str(used), str(max(0, total - used))) if total is not None else ("", "", "")
            (pending["设备内存总量_源值"],
             pending["设备内存已用_源值"],
             pending["设备内存可用_源值"]) = pair
            if pair[0]:
                pending["采集状态"] = "成功"
            pending = None
    return devices


def parse_python_version(raw):
    match = re.search(r"\bPython\s+([0-9][^\s]*)", raw)
    return match.group(1) if match else ""


def parse_docker_cli_version(raw):
    match = re.search(r"\bDocker\s+version\s+([^,\s]+)", raw, re.IGNORECASE)
    return match.group(1) if match else ""


def empty_reports():
    return {name: [] for name in REPORT_FIELDS}


def _excel_safe(value):
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def write_reports(reports, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, fields in REPORT_FIELDS.items():
        with (output_dir / (name + ".csv")).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in reports.get(name, []):
                writer.writerow({field: _excel_safe(row.get(field, "")) for field in fields})


def _section(sections, name):
    return sections.get(name, Section("", 127))


def _add_issue(reports, target, stage, code, message, advice, level="提示"):
    reports["issues"].append({
        "目标地址": target, "阶段": stage, "级别": level,
        "问题代码": code, "说明": message[:300], "建议": advice,
    })


def _failure_code(section):
    output = section.output.lower()
    if "permission denied" in output or "operation not permitted" in output:
        return "PERMISSION_DENIED"
    if "does not support" in output or "not supported" in output:
        return "UNSUPPORTED"
    if section.returncode == 127 or "not found" in output:
        return "NOT_INSTALLED"
    return "COMMAND_FAILED"


def _base_probe_script(model_dir):
    """Generate fixed read-only shell commands; only a quoted path varies."""
    lines = [
        "export LC_ALL=C",
        "run() { key=$1; shift; printf '__LLM_BEGIN__%s\\n' \"$key\"; \"$@\" 2>&1; rc=$?; printf '__LLM_END__%s:%s\\n' \"$key\" \"$rc\"; }",
        "run hostname hostname",
        "run os_release cat /etc/os-release",
        "run kernel uname -r",
        "run arch uname -m",
        "run lscpu lscpu",
        "run meminfo cat /proc/meminfo",
        "run df df -P -B1 -T",
        "run ipv4 ip -o -4 addr show",
        "run net_link ip -o link show",
        "run net_speed sh -c 'for p in /sys/class/net/*/speed; do [ -f \"$p\" ] || continue; n=${p%/speed}; n=${n##*/}; v=$(cat \"$p\" 2>/dev/null); printf \"%s %s\\n\" \"$n\" \"$v\"; done'",
        "run python3_path sh -c 'command -v python3'",
        "run python3_version python3 -V",
        "run python_path sh -c 'command -v python'",
        "run python_version python -V",
        "run docker_version docker --version",
        "run nvidia_query nvidia-smi --query-gpu=index,uuid,name,memory.total,memory.used,memory.free,utilization.gpu,driver_version,pci.bus_id --format=csv,noheader,nounits",
        "run nvidia_basic nvidia-smi -L",
        "run nvidia_mig nvidia-smi --query-gpu=index,mig.mode.current --format=csv,noheader",
        "run nvidia_cuda nvidia-smi",
        "run nvidia_topo nvidia-smi topo -m",
        "run cuda_toolkit nvcc --version",
        "run npu_info npu-smi info",
        "run npu_list npu-smi info -l",
        "run npu_topo npu-smi info -t topo",
        "run cann_version sh -c 'found=0; for p in /usr/local/Ascend/ascend-toolkit/latest/*-linux/ascend_toolkit_install.info /usr/local/Ascend/cann/*-linux/ascend_toolkit_install.info \"$HOME\"/Ascend/ascend-toolkit/latest/*-linux/ascend_toolkit_install.info \"$HOME\"/Ascend/cann/*-linux/ascend_toolkit_install.info; do if [ -f \"$p\" ]; then printf \"PATH:%s\\n\" \"$p\"; head -n 30 \"$p\"; found=1; fi; done; [ \"$found\" -eq 1 ]'",
    ]
    if model_dir:
        quoted = shlex.quote(model_dir)
        lines.extend(("run model_exists test -d " + quoted,
                      "run model_df df -P -B1 -T -- " + quoted))
    return "\n".join(lines) + "\n"


def _board_probe_script(ids):
    lines = [
        "export LC_ALL=C",
        "run() { key=$1; shift; printf '__LLM_BEGIN__%s\\n' \"$key\"; \"$@\" 2>&1; rc=$?; printf '__LLM_END__%s:%s\\n' \"$key\" \"$rc\"; }",
    ]
    for item in ids:
        if re.fullmatch(r"[0-9]+", str(item)):
            lines.append("run npu_board_{0} npu-smi info -t board -i {0}".format(item))
    return "\n".join(lines) + "\n"


def run_ssh(target, script, connect_timeout, command_timeout):
    command = [
        "ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout={}".format(connect_timeout), target, "sh", "-s",
    ]
    try:
        return subprocess.run(command, input=script, text=True, encoding="utf-8",
                              errors="replace", capture_output=True, timeout=command_timeout,
                              check=False)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, "", "SSH/查询超时")
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))


def _os_name(raw):
    for line in raw.splitlines():
        if line.startswith("PRETTY_NAME="):
            value = line.split("=", 1)[1].strip()
            if value[:1] == value[-1:] and value[:1] in ("'", '"'):
                value = value[1:-1]
            return value
    return ""


def _colon_fields(raw):
    return {key.strip(): value.strip() for line in raw.splitlines()
            if ":" in line for key, value in [line.split(":", 1)]}


def _gib_from_kib(text):
    match = re.search(r"(\d+)", text)
    return "{:.2f}".format(int(match.group(1)) / 1048576) if match else ""


def _gib_from_bytes(text):
    return "{:.2f}".format(int(text) / 1073741824) if text.isdigit() else ""


def _storage_rows(raw, target, model_dir, model_mount, model_exists):
    rows = []
    excluded = {"tmpfs", "devtmpfs", "overlay", "proc", "sysfs", "cgroup", "cgroup2",
                "squashfs", "autofs", "securityfs", "debugfs", "tracefs", "fusectl"}
    for line in raw.splitlines()[1:]:
        columns = line.split(None, 6)
        if len(columns) != 7 or columns[1] in excluded:
            continue
        filesystem, kind, size, _used, available, _percent, mount = columns
        rows.append({
            "目标地址": target, "挂载点": mount, "文件系统类型": kind,
            "总量_GiB": _gib_from_bytes(size), "可用_GiB": _gib_from_bytes(available),
            "模型目录是否存在": ("是" if model_exists else "否") if model_dir else "未指定",
            "是否承载模型目录": ("是" if mount == model_mount else "否") if model_dir and model_exists else "未指定",
        })
    return rows


def _parse_topology(raw, target):
    lines = [line.split() for line in raw.splitlines() if line.strip()]
    header = next((row for row in lines if len(row) >= 2 and row[0] == "GPU0" and row[1].startswith("GPU")), [])
    devices = [part for part in header if re.fullmatch(r"GPU\d+", part)]
    result = []
    for row in lines:
        if row is header or not row or row[0] not in devices or len(row) < len(devices) + 1:
            continue
        for position, other in enumerate(devices):
            if int(row[0][3:]) < int(other[3:]):
                result.append({"目标地址": target, "设备A": row[0], "设备B": other,
                               "连接类型": row[position + 1], "查询状态": "已读取", "原始摘要": ""})
    return result


def _network_summary(addresses, links, speeds):
    ipv4 = []
    for line in addresses.splitlines():
        match = re.search(r"^\d+:\s+(\S+)\s+inet\s+([0-9.]+/\d+)", line)
        if match:
            ipv4.append("{}={}".format(match.group(1), match.group(2)))
    speed_map = {}
    for line in speeds.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit() and int(parts[1]) > 0:
            speed_map[parts[0]] = parts[1] + "Mb/s"
    summary = []
    for line in links.splitlines():
        match = re.search(r"^\d+:\s+([^:@\s]+).*?\bstate\s+(\S+)", line)
        if match and match.group(1) != "lo":
            name = match.group(1)
            summary.append("{} {} {}".format(name, match.group(2), speed_map.get(name, "速率未知")))
    return "; ".join(ipv4), "; ".join(summary)


def _cann_version(raw):
    found = re.findall(r"(?im)^(?:Version|CANN_VERSION|version_dir)\s*[:=]\s*([^\s]+)", raw)
    return "; ".join(dict.fromkeys(found))


def collect_target(target, timestamp, model_dir, reports, runner=run_ssh,
                   connect_timeout=10, command_timeout=60):
    """Collect one explicit target; append partial results and classified issues."""
    validate_target(target)
    label = target.rsplit("@", 1)[-1]
    server = {"采集时间": timestamp, "目标地址": label, "采集状态": "失败"}
    reports["servers"].append(server)
    result = runner(target, _base_probe_script(model_dir), connect_timeout, command_timeout)
    sections = parse_sections(result.stdout or "")
    if not sections:
        _add_issue(reports, label, "SSH", "SSH_FAILED", (result.stderr or "无命令输出").strip(),
                   "检查内网连通性、SSH 密钥和 known_hosts", "错误")
        return

    server["主机名"] = _section(sections, "hostname").output
    server["系统"] = _os_name(_section(sections, "os_release").output)
    server["内核"] = _section(sections, "kernel").output
    server["架构"] = _section(sections, "arch").output
    cpu = _colon_fields(_section(sections, "lscpu").output)
    server["CPU型号"] = cpu.get("Model name", cpu.get("Architecture", ""))
    server["CPU逻辑核数"] = cpu.get("CPU(s)", "")
    server["CPU插槽数"] = cpu.get("Socket(s)", "")
    server["NUMA节点数"] = cpu.get("NUMA node(s)", "")
    memory = _colon_fields(_section(sections, "meminfo").output)
    server["内存总量_GiB"] = _gib_from_kib(memory.get("MemTotal", ""))
    server["内存可用_GiB"] = _gib_from_kib(memory.get("MemAvailable", ""))
    server["IPv4摘要"], server["网络链路摘要"] = _network_summary(
        _section(sections, "ipv4").output,
        _section(sections, "net_link").output,
        _section(sections, "net_speed").output,
    )
    if not server["主机名"] or not server["架构"] or not server["内存总量_GiB"]:
        _add_issue(reports, label, "主机", "PARSE_FAILED", "主机核心字段缺失", "核对 hostname、uname 和 /proc/meminfo 输出")

    exists = bool(model_dir and _section(sections, "model_exists").returncode == 0)
    model_df = _section(sections, "model_df").output.splitlines()
    model_mount = model_df[1].split(None, 6)[-1] if len(model_df) > 1 and exists else ""
    reports["storage"].extend(_storage_rows(_section(sections, "df").output, label,
                                           model_dir, model_mount, exists))
    if _section(sections, "df").returncode != 0:
        _add_issue(reports, label, "存储", "COMMAND_FAILED", "文件系统查询失败", "检查 df 命令和权限")
    if model_dir and not exists:
        _add_issue(reports, label, "模型目录", "NOT_DETECTED", "模型目录不存在：" + model_dir,
                   "核对 --model-dir 或目标机挂载点")

    for prefix in ("python3", "python"):
        path = _section(sections, prefix + "_path")
        version = _section(sections, prefix + "_version")
        server[prefix + "路径"] = path.output if path.returncode == 0 else ""
        code = _failure_code(version) if version.returncode != 0 else ""
        server[prefix + "版本"] = (parse_python_version(version.output) if version.returncode == 0
                                    else ("未安装" if code == "NOT_INSTALLED" else "查询失败"))
        if version.returncode != 0:
            _add_issue(reports, label, "Python", code, prefix + " 命令不可用：" + version.output,
                       "核对当前 SSH 用户的 PATH")
    docker = _section(sections, "docker_version")
    docker_code = _failure_code(docker) if docker.returncode != 0 else ""
    server["Docker_CLI版本"] = (parse_docker_cli_version(docker.output) if docker.returncode == 0
                              else ("未安装" if docker_code == "NOT_INSTALLED" else "查询失败"))
    if docker.returncode != 0:
        _add_issue(reports, label, "Docker", docker_code, "docker CLI 命令不可用：" + docker.output,
                   "核对 Docker CLI 安装及当前 SSH 用户的 PATH")
    elif not server["Docker_CLI版本"]:
        server["Docker_CLI版本"] = "查询失败"
        _add_issue(reports, label, "Docker", "PARSE_FAILED", "无法解析 docker --version 输出",
                   "核对当前 Docker CLI 输出格式")

    devices = []
    nvidia = _section(sections, "nvidia_query")
    if nvidia.returncode == 0 and nvidia.output:
        try:
            devices.extend(parse_nvidia_csv(nvidia.output, label))
        except ValueError as exc:
            _add_issue(reports, label, "NVIDIA", "PARSE_FAILED", str(exc),
                       "保存 nvidia-smi 原始输出并核对驱动版本")
        mig = _section(sections, "nvidia_mig")
        if mig.returncode == 0:
            modes = {}
            for row in csv.reader(io.StringIO(mig.output)):
                if len(row) == 2:
                    modes[row[0].strip()] = row[1].strip()
            for device in devices:
                if device["厂商"] == "NVIDIA":
                    device["分区状态"] = modes.get(device["设备编号"], "未检测")
        topo = _section(sections, "nvidia_topo")
        if topo.returncode == 0:
            reports["topology"].extend(_parse_topology(topo.output, label))
    elif _section(sections, "nvidia_basic").returncode == 0 and "No devices" not in _section(sections, "nvidia_basic").output:
        _add_issue(reports, label, "NVIDIA", "COMMAND_FAILED", "设备可列出，但结构化查询失败",
                   "核对 nvidia-smi --help-query-gpu 与驱动版本")
    elif nvidia.returncode != 0 and _failure_code(nvidia) == "PERMISSION_DENIED":
        _add_issue(reports, label, "NVIDIA", "PERMISSION_DENIED", "GPU 查询权限不足：" + nvidia.output,
                   "核对设备节点和当前 SSH 用户权限")

    cuda = _section(sections, "nvidia_cuda")
    match = re.search(r"CUDA Version:\s*([0-9.]+)", cuda.output)
    if match and any(device["厂商"] == "NVIDIA" for device in devices):
        server["CUDA驱动支持版本"] = match.group(1)
    nvcc = _section(sections, "cuda_toolkit")
    match = re.search(r"\brelease\s+([0-9.]+)", nvcc.output)
    if match and any(device["厂商"] == "NVIDIA" for device in devices):
        server["CUDA_Toolkit版本"] = match.group(1)

    ascend = _section(sections, "npu_info")
    if ascend.returncode == 0 and ascend.output:
        parsed = parse_ascend_info(ascend.output, label)
        if not parsed:
            _add_issue(reports, label, "昇腾", "PARSE_FAILED", "npu-smi 返回内容无法识别设备行",
                       "用现场 npu-smi info 输出核对当前型号格式")
        devices.extend(parsed)
        topo = _section(sections, "npu_topo")
        unsupported_text = "does not support" in topo.output.lower() or "not supported" in topo.output.lower()
        topo_code = _failure_code(topo) if topo.returncode != 0 or unsupported_text else ""
        topo_status = {"UNSUPPORTED": "不支持", "PERMISSION_DENIED": "权限不足",
                       "NOT_INSTALLED": "命令缺失"}.get(topo_code, "查询失败")
        reports["topology"].append({
            "目标地址": label, "设备A": "", "设备B": "", "连接类型": "",
            "查询状态": "已读取，需核对" if not topo_code else topo_status,
            "原始摘要": " ".join(topo.output.split())[:500],
        })
        if topo_code:
            _add_issue(reports, label, "拓扑", topo_code, "昇腾拓扑查询不可用：" + topo.output,
                       "核对设备型号与 npu-smi 支持范围")
        cann = _section(sections, "cann_version")
        server["CANN版本"] = _cann_version(cann.output) if cann.returncode == 0 else ""
        if not server["CANN版本"]:
            _add_issue(reports, label, "CANN", "NOT_DETECTED", "宿主机未检测到 CANN 版本",
                       "核对安装路径；若只安装在容器内，需另行采集")

        ids = sorted({device["设备编号"] for device in parsed}, key=int)
        if ids:
            board_result = runner(target, _board_probe_script(ids), connect_timeout, command_timeout)
            boards = parse_sections(board_result.stdout or "")
            for device in parsed:
                board = _section(boards, "npu_board_" + device["设备编号"])
                if board.returncode == 0:
                    properties = _colon_fields(board.output)
                    device["驱动版本"] = properties.get("Software Version", "")
                    device["固件版本"] = properties.get("Firmware Version", "")
                    if properties.get("Compatibility") == "NOK":
                        _add_issue(reports, label, "昇腾", "COMMAND_FAILED", "驱动与固件兼容性为 NOK",
                                   "核对当前设备的驱动固件配套关系", "错误")
                else:
                    device["采集状态"] = "部分成功"
                    _add_issue(reports, label, "昇腾", _failure_code(board),
                               "设备 {} 的驱动/固件版本查询失败：{}".format(device["设备编号"], board.output),
                               "核对 npu-smi info -t board 的权限和支持范围")
    elif ascend.returncode != 0 and _failure_code(ascend) == "PERMISSION_DENIED":
        _add_issue(reports, label, "昇腾", "PERMISSION_DENIED", "NPU 查询权限不足：" + ascend.output,
                   "核对设备节点和当前 SSH 用户权限")

    reports["accelerators"].extend(devices)
    vendors = sorted({device["厂商"] for device in devices})
    server["加速卡厂商"] = "/".join(vendors) if vendors else "未检测到"
    server["加速卡数量"] = str(len(devices))
    if not devices:
        _add_issue(reports, label, "加速卡", "NOT_DETECTED", "未解析到 NVIDIA GPU 或昇腾 NPU",
                   "核对硬件、驱动和当前用户查询权限")
    own_issues = [item for item in reports["issues"] if item["目标地址"] == label]
    server["采集状态"] = "部分成功" if own_issues else "成功"


def main(argv=None):
    parser = argparse.ArgumentParser(description="通过 SSH 盘点昇腾/NVIDIA Linux 服务器并生成 CSV")
    parser.add_argument("--hosts", required=True, help="逗号分隔的 SSH 目标，例如 ops@10.20.1.11,ops@10.20.1.12")
    parser.add_argument("--model-dir", default="", help="可选：目标机上的模型目录")
    parser.add_argument("--output", default="./llm_inventory", help="本地 CSV 输出目录")
    parser.add_argument("--connect-timeout", type=int, default=10, help="SSH 连接超时秒数")
    parser.add_argument("--command-timeout", type=int, default=60, help="单次 SSH 查询超时秒数")
    args = parser.parse_args(argv)
    if args.connect_timeout < 1 or args.command_timeout < 1:
        parser.error("超时参数必须大于 0")
    try:
        targets = list(dict.fromkeys(validate_target(item.strip()) for item in args.hosts.split(",")))
        validate_model_dir(args.model_dir)
    except ValueError as exc:
        parser.error(str(exc))
    reports = empty_reports()
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    for target in targets:
        collect_target(target, timestamp, args.model_dir, reports, run_ssh,
                       args.connect_timeout, args.command_timeout)
        print("{}：{}".format(target, reports["servers"][-1]["采集状态"]))
    write_reports(reports, Path(args.output))
    counts = {state: sum(row["采集状态"] == state for row in reports["servers"])
              for state in ("成功", "部分成功", "失败")}
    print("输出目录：{}；成功 {} 台，部分成功 {} 台，失败 {} 台".format(
        Path(args.output).resolve(), counts["成功"], counts["部分成功"], counts["失败"]))
    return 2 if counts["失败"] else (1 if counts["部分成功"] else 0)


if __name__ == "__main__":
    sys.exit(main())
