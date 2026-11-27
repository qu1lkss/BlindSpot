from collections import Counter
import json


def metric_rows(truth, prediction, classes):
    if not truth or len(truth) != len(prediction):
        raise ValueError("Нужна одна метка на каждый объект.")
    counts = Counter(zip(truth,prediction))
    allowed = set(classes)
    if any(t not in allowed or p not in allowed for t,p in counts):
        raise ValueError("В прогнозах обнаружена неизвестная метка.")
    rows = []

    def add(metric,value,actual=None,predicted=None,reason=None):
        def encode(label):
            return json.dumps(label,ensure_ascii=False) if label is not None else ""
        rows.append(dict(metric=metric,value=value,actual=encode(actual),predicted=encode(predicted),reason=reason))

    add("accuracy",sum(counts[c,c] for c in classes)/len(truth))
    f1 = []
    missing = []
    for c in classes:
        positives = sum(counts[c,k] for k in classes)
        guesses = sum(counts[k,c] for k in classes)
        add("recall",counts[c,c]/positives if positives else None,actual=c,
            reason=None if positives else "Класс отсутствует среди правильных ответов.")
        if positives + guesses:
            f1.append(2*counts[c,c]/(positives+guesses))
        else:
            missing.append(str(c))
        for k in classes:
            add("confusion_matrix",counts[c,k],c,k)
    add("macro_f1",None if missing else sum(f1)/len(classes),
        reason="Нет наблюдений и прогнозов для классов: " + ", ".join(missing) if missing else None)
    return rows
