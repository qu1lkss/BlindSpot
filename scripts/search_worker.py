"""Запуск проверки поискового контейнера с доверенного хоста Compose."""

import argparse
import json
import subprocess
from uuid import UUID, uuid4


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], check=True, text=True, stdout=subprocess.PIPE,
                          **kwargs).stdout.strip()


def launch(study_id, project, control_id_sha256=None):
    study_id = str(UUID(study_id))
    controller = docker("compose", "-p", project, "ps", "-q", "controller")
    if not controller:
        raise ValueError("Сначала запустите контроллер Compose.")
    container = json.loads(docker("inspect", controller))[0]
    volume = next(m["Name"] for m in container["Mounts"]
                  if m["Type"] == "volume" and m["Destination"] == "/var/lib/blindspot")
    job = json.loads(docker("exec", controller, "python", "-m", "blindspot.isolation", "spec", study_id))
    job["task_id"] = str(uuid4())
    job["control_id_sha256"] = control_id_sha256
    name = "blindspot-search-" + job["task_id"]
    version = "versions/" + study_id
    mount = f"type=volume,src={volume},volume-nocopy,volume-subpath={version}/"
    try:
        output = docker(
            "run", "--rm", "-i", "--pull", "never", "--name", name, "--network", "none",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "10001:10001", "--cpus", "1", "--memory", f"{job['memory_mb']}m",
            "--memory-swap", f"{job['memory_mb']}m", "--pids-limit", "128",
            "--env", "OPENBLAS_NUM_THREADS=1", "--env", "OMP_NUM_THREADS=1",
            "--mount", mount + "prepared/search,dst=/input,readonly",
            "--mount", mount + "search-work,dst=/index",
            "--tmpfs", f"/work:rw,nosuid,nodev,mode=0700,uid=10001,gid=10001,size={job['disk_mb']}m",
            container["Image"], "python", "-m", "blindspot.isolation", "check",
            input=json.dumps(job), timeout=job["seconds"],
        )
    except (KeyboardInterrupt, subprocess.TimeoutExpired):
        subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        raise
    return json.loads(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("id", help="UUID подготовленной версии")
    parser.add_argument("--project", default="blindspot", help="Имя проекта Compose")
    parser.add_argument("--control-id-sha256", help="Хеш контрольного ID для испытания изоляции")
    args = parser.parse_args()
    print(json.dumps(launch(args.id, args.project, args.control_id_sha256), ensure_ascii=False, indent=2))
