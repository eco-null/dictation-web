"use strict";

const uiStrings = JSON.parse(document.getElementById("ui-strings").textContent);
const tr = (key) => uiStrings[key] || key;

function showStatus(element, message, error = false) {
  element.textContent = message;
  element.classList.toggle("is-error", error);
}

async function requestJSON(url, options) {
  let response;
  try {
    response = await fetch(url, options);
  } catch {
    const error = new Error(tr("Could not reach the server."));
    error.transient = true;
    throw error;
  }
  const data = await response.json().catch(() => null);
  if (response.ok && data !== null) return data;
  const message = {
    401: "Your session expired. Sign in again.",
    409: "Another job is running. Try again when it finishes.",
    413: "This file is larger than the upload limit.",
  }[response.status];
  const detail = typeof data?.detail === "string" ? data.detail : "";
  const error = new Error(message ? tr(message) : detail || tr("The request failed."));
  error.transient = response.status >= 500;
  throw error;
}

function jsonOptions(method, payload) {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function pollJob(jobId, onStage) {
  if (!/^[a-f0-9]{32}$/.test(jobId || "")) throw new Error(tr("The request failed."));
  let failures = 0;
  for (;;) {
    let job;
    try {
      job = await requestJSON("/api/jobs/" + jobId, { cache: "no-store" });
      failures = 0;
    } catch (error) {
      if (!error.transient || ++failures > 3) throw error;
      onStage(tr("Connection lost. Retrying…"));
      await wait(900 * failures);
      continue;
    }
    if (job.status === "done") return job.result || {};
    if (job.status === "failed") throw new Error(job.error || tr("The request failed."));
    if (job.status !== "queued" && job.status !== "running") {
      throw new Error(tr("The request failed."));
    }
    onStage(job.stage || tr("Working…"));
    await wait(900);
  }
}

async function startJob(url, options, onStage) {
  const { job_id: id } = await requestJSON(url, options);
  return { id, data: await pollJob(id, onStage) };
}

function displayTranscript({ id, data }) {
  const result = document.getElementById("result");
  const text = result.querySelector("textarea");
  const warning = document.getElementById("result-warning");
  document.getElementById("result-label").textContent = tr(data.mode === "ask" ? "Assistant answer" : "Transcript");
  text.value = data.text || "";
  result.querySelector("[data-copy]").disabled = !text.value;
  warning.textContent = data.warning || (!text.value ? tr("No transcript was returned.") : "");
  warning.hidden = !warning.textContent;
  for (const button of result.querySelectorAll("[data-download]")) {
    button.dataset.jobId = id;
    button.disabled = !text.value;
    if (button.dataset.fmt === "srt") button.hidden = !data.srt;
  }
  const raw = result.querySelector("[data-raw]");
  raw.hidden = !data.raw || data.raw === data.text;
  raw.textContent = tr("Original text");
  raw.onclick = () => {
    const original = raw.textContent === tr("Original text");
    text.value = original ? data.raw : data.text;
    raw.textContent = tr(original ? "Processed text" : "Original text");
  };
  result.hidden = false;
}

function copyText(text) {
  if (navigator.clipboard) return navigator.clipboard.writeText(text);
  const field = document.createElement("textarea");
  field.className = "clipboard-field";
  field.value = text;
  document.body.append(field);
  try {
    field.select();
    if (!document.execCommand("copy")) throw new Error();
  } finally {
    field.remove();
  }
}

document.addEventListener("click", async (event) => {
  const copy = event.target.closest("[data-copy]");
  if (copy) {
    const source = document.querySelector(copy.dataset.target);
    if (!source || copy.dataset.copying) return;
    const label = copy.textContent;
    copy.dataset.copying = "true";
    try {
      await copyText(source.value ?? source.textContent);
      copy.textContent = tr("Copied");
    } catch {
      copy.textContent = tr("Copy failed");
    }
    copy.setAttribute("aria-live", "polite");
    setTimeout(() => {
      copy.textContent = label;
      delete copy.dataset.copying;
    }, 1800);
  }
  const download = event.target.closest("[data-download]");
  if (download?.dataset.jobId) {
    const url = "/api/jobs/" + download.dataset.jobId + "/download?fmt=" + download.dataset.fmt;
    window.location.assign(url);
  }
});

for (const form of document.querySelectorAll("[data-job-form]")) {
  const input = form.elements.file;
  const dropzone = form.querySelector("[data-dropzone]");
  const label = dropzone.querySelector("[data-file-label]");
  const prompt = label.textContent;
  const stage = form.querySelector("[data-stage]");
  const submit = form.querySelector("button[type=submit]");
  const timestampToggle = form.elements.timestamps;
  if (timestampToggle) {
    const option = form.querySelector("[data-subtitle-option]");
    const refresh = () => { option.hidden = !timestampToggle.checked; };
    timestampToggle.addEventListener("change", refresh);
    refresh();
  }
  input.addEventListener("change", () => { label.textContent = input.files[0]?.name || prompt; });
  dropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    if (!submit.disabled) dropzone.classList.add("over");
  });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("over"));
  dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropzone.classList.remove("over");
    if (submit.disabled || !event.dataTransfer.files.length) return;
    const transfer = new DataTransfer();
    transfer.items.add(event.dataTransfer.files[0]);
    input.files = transfer.files;
    label.textContent = input.files[0].name;
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submit.disabled) return;
    const body = new FormData(form);
    if (timestampToggle) body.set("wrap", form.querySelector("[data-subtitle-option] input").checked ? "on" : "off");
    submit.disabled = true;
    input.disabled = true;
    showStatus(stage, tr("Uploading…"));
    try {
      const result = await startJob(form.action, { method: "POST", body },
        (message) => showStatus(stage, message));
      if (form.hasAttribute("data-meeting-form")) {
        location.assign("/meetings/" + encodeURIComponent(result.data.base));
        return;
      }
      displayTranscript(result);
      showStatus(stage, tr("Transcription complete."));
    } catch (error) {
      showStatus(stage, error.message, true);
    } finally {
      submit.disabled = false;
      input.disabled = false;
    }
  });
}

const meetings = document.querySelector("[data-meetings]");
meetings?.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-retry], [data-delete-meeting]");
  if (!button || button.disabled) return;
  const stage = meetings.querySelector("[data-meeting-stage]");
  const base = button.dataset.retry || button.dataset.deleteMeeting;
  const url = "/api/meetings/" + encodeURIComponent(base);
  if (!button.dataset.retry && !confirm(tr("Delete this meeting and its recording?"))) return;
  const buttons = meetings.querySelectorAll("[data-retry], [data-delete-meeting]");
  buttons.forEach((item) => { item.disabled = true; });
  try {
    if (button.dataset.retry) {
      showStatus(stage, tr("Working…"));
      await startJob(url + "/retry", { method: "POST" }, (message) => showStatus(stage, message));
      location.assign("/meetings/" + encodeURIComponent(base));
    } else {
      await requestJSON(url, { method: "DELETE" });
      location.reload();
    }
  } catch (error) {
    showStatus(stage, error.message, true);
  } finally {
    buttons.forEach((item) => { item.disabled = false; });
  }
});

const historyPanel = document.querySelector("[data-history]");
const search = document.getElementById("history-search");
if (historyPanel && search) {
  const rows = [...historyPanel.querySelectorAll("[data-history-row]")];
  const clear = historyPanel.querySelector("#clear");
  const remove = historyPanel.querySelector("#delete-selected");
  const stage = historyPanel.querySelector("#stage");
  const locale = document.documentElement.lang;
  const searchable = rows.map((row) => {
    const entry = JSON.parse(row.querySelector(".row-check").value);
    return [entry.text, entry.raw, entry.ts, entry.model].join(" ").toLocaleLowerCase(locale);
  });
  let busy = false;
  const selected = () => [...historyPanel.querySelectorAll(".row-check:checked")];
  function refreshDelete() { remove.disabled = busy || !selected().length; }
  search.addEventListener("input", () => {
    const query = search.value.trim().toLocaleLowerCase(locale);
    rows.forEach((row, index) => {
      row.hidden = !searchable[index].includes(query);
      if (row.hidden) row.querySelector(".row-check").checked = false;
    });
    document.getElementById("empty-search").hidden = rows.some((row) => !row.hidden);
    refreshDelete();
  });
  historyPanel.addEventListener("change", refreshDelete);
  async function deleteRows(all) {
    if (busy || !confirm(tr(all ? "Clear all dictation history?" : "Delete selected dictations?"))) return;
    busy = true;
    clear.disabled = true;
    refreshDelete();
    try {
      if (all) {
        await requestJSON("/api/history/clear", { method: "POST" });
      } else {
        const entries = selected().map((checkbox) => JSON.parse(checkbox.value));
        await requestJSON("/api/history", jsonOptions("DELETE", { rows: entries }));
      }
      location.reload();
    } catch (error) {
      showStatus(stage, error.message, true);
      busy = false;
      clear.disabled = false;
      refreshDelete();
    }
  }
  clear.addEventListener("click", () => deleteRows(true));
  remove.addEventListener("click", () => deleteRows(false));
}

const agent = document.getElementById("agent-form");
if (agent) {
  const question = agent.elements.question;
  const chat = document.getElementById("chat");
  const stage = document.getElementById("stage");
  const submit = agent.querySelector("button[type=submit]");
  let messageCount = 0;
  function addMessage(text, isAnswer) {
    document.getElementById("empty-chat")?.remove();
    const item = document.createElement("article");
    item.className = isAnswer ? "chat-bubble bubble-assistant" : "chat-bubble bubble-user";
    const title = document.createElement("strong");
    title.textContent = tr(isAnswer ? "Assistant" : "You");
    const content = document.createElement("p");
    content.id = "message-" + ++messageCount;
    content.textContent = text;
    item.append(title, content);
    if (isAnswer) {
      const copy = document.createElement("button");
      copy.className = "btn btn-sm btn-ghost";
      copy.type = "button";
      copy.dataset.copy = "";
      copy.dataset.target = "#" + content.id;
      copy.textContent = tr("Copy");
      item.append(copy);
    }
    chat.append(item);
    chat.scrollTop = chat.scrollHeight;
  }
  agent.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submit.disabled || !question.value.trim()) return;
    const payload = Object.fromEntries(new FormData(agent));
    submit.disabled = true;
    question.readOnly = true;
    showStatus(stage, tr("Thinking…"));
    try {
      const result = await requestJSON("/api/agent", jsonOptions("POST", payload));
      addMessage(payload.question.trim(), false);
      addMessage(result.answer, true);
      question.value = "";
      showStatus(stage, "");
    } catch (error) {
      showStatus(stage, error.message, true);
    } finally {
      question.readOnly = false;
      submit.disabled = false;
      question.focus();
    }
  });
}
