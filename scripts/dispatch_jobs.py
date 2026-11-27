"""Доверенный диспетчер Docker. Запускается на хосте из каталога проекта."""
import argparse
import json
import subprocess
from time import sleep

from search_worker import docker, launch


def process(project):
    def command(action, *ids, body=None):
        return json.loads(docker("compose","-p",project,"exec","-T","controller","python","-m",
                                 "blindspot.dispatch",action,*ids,input=json.dumps(body)))
    job = command("claim")
    if not job:
        return False
    study, task = job["research_id"], job["id"]
    try:
        result = launch(study, project, action="baseline", task=task, deadline=job["deadline"])
        command("publish",study,task,body=result)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        reason = "Рабочий процесс не завершил расчёт."
        if isinstance(error, subprocess.TimeoutExpired):
            reason = "Истёк срок базового расчёта."
        elif isinstance(error, subprocess.CalledProcessError) and error.stderr:
            reason = error.stderr.strip().splitlines()[-1][:400]
        command("fail", study, task, body={"reason": reason})
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project",default="blindspot")
    parser.add_argument("--once",action="store_true")
    args = parser.parse_args()
    while True:
        worked = process(args.project)
        if args.once:
            break
        if not worked:
            sleep(1)
