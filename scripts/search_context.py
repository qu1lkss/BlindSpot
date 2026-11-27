"""Извлечь контекст в поисковом контейнере выбранной версии."""
import argparse
import json

from search_worker import launch


def search(study, project, method, feature, query):
    return launch(study, project, action="context",
                  extra={"method": method, "feature": feature, "query": query})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study")
    parser.add_argument("--project", default="blindspot")
    parser.add_argument("--method", required=True)
    parser.add_argument("--feature", required=True)
    parser.add_argument("--query", required=True)
    args = parser.parse_args()
    print(json.dumps(search(args.study, args.project, args.method, args.feature, args.query),
                     ensure_ascii=False, indent=2))
