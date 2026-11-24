const statusLine = document.querySelector("#status");
const studies = document.querySelector("#studies");
const refresh = document.querySelector("#refresh");

async function loadStudies() {
  refresh.disabled = true;
  statusLine.textContent = "Загрузка исследований…";
  try {
    const response = await fetch("/studies", { cache: "no-store" });
    if (!response.ok) throw new Error();
    const { items } = await response.json();
    const entries = items.map((study) => {
      const entry = document.createElement("li");
      entry.textContent = [study.id, study.stage, study.created_at].join(" · ");
      return entry;
    });
    studies.replaceChildren(...entries);
    statusLine.textContent = items.length ? "" : "Сохранённых исследований пока нет.";
  } catch {
    studies.replaceChildren();
    statusLine.textContent = "Не удалось загрузить исследования. Попробуйте обновить список.";
  } finally {
    refresh.disabled = false;
  }
}

refresh.addEventListener("click", loadStudies);
loadStudies();
