import hashlib
import json


def completed_history(store, study):
    """В IPC уходит сводка завершённых F1-опытов, без исходных строк."""
    history = []
    with store.connection() as db:
        experiments = db.execute("""
            SELECT e.id,e.rows_count,a.path,a.sha256 FROM experiment e
            JOIN artifact a ON a.research_id=e.research_id AND a.id=e.id
            WHERE e.research_id=? AND e.kind='baseline' AND e.status='сохранён'
              AND a.kind='predictions' AND a.ready=1 ORDER BY e.id
        """, (study,)).fetchall()
        for row in experiments:
            summary = dict(db.execute("""
                SELECT metric,value FROM measurement WHERE research_id=? AND experiment_id=?
                  AND scope='F1' AND metric IN ('accuracy','macro_f1')
            """, (study,row["id"])).fetchall())
            summary["rows"] = row["rows_count"]
            text = json.dumps(summary, ensure_ascii=False, sort_keys=True)
            version = hashlib.sha256((row["sha256"] + text).encode()).hexdigest()
            history.append({"study_id":study, "partition":"F1", "status":"completed", "id":row["id"],
                            "version":version, "method":"baseline", "feature":None, "origin":row["path"],
                            "summary":text})
    return history
