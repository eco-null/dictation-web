"use strict";

const settingsForm = document.getElementById("settings-form");
const save = document.getElementById("save-settings");
const saveStatus = document.getElementById("save-status");
const updates = new Map();
let saving = false;

function markChanged(name, value) {
  updates.set(name, value);
  save.disabled = saving;
  showStatus(saveStatus, tr("Unsaved changes"));
}

for (const service of settingsForm.querySelectorAll("[data-service]")) {
  const provider = service.querySelector(".provider-select-el");
  provider.addEventListener("change", () => {
    for (const block of service.querySelectorAll("[data-provider]")) {
      block.hidden = block.dataset.provider !== provider.value;
    }
  });
}

settingsForm.addEventListener("input", ({ target }) => {
  if (!target.name) return;
  if (target.name === "ui_theme") document.documentElement.dataset.theme = target.value;
  if (target.hasAttribute("data-secret") && !target.value) {
    updates.delete(target.name);
    save.disabled = !updates.size || saving;
    if (!updates.size) showStatus(saveStatus, "");
    return;
  }
  markChanged(target.name, target.dataset.kind === "bool" ? target.value === "on" : target.value);
});

settingsForm.addEventListener("click", async (event) => {
  const clear = event.target.closest("[data-clear-key]");
  if (clear) {
    if (!confirm(tr("Remove this saved API key?"))) return;
    settingsForm.elements[clear.dataset.clearKey].value = "";
    markChanged(clear.dataset.clearKey, null);
  }
  const button = event.target.closest("[data-model-refresh]");
  if (!button || button.disabled) return;
  const block = button.closest("[data-provider]");
  const service = block.closest("[data-service]").dataset.service;
  const input = block.querySelector("[data-model-input]");
  const status = block.querySelector("[data-model-stage]");
  if ([...updates.keys()].some((key) => key === service + "_" + block.dataset.provider + "_key"
    || key === service + "_" + block.dataset.provider + "_url")) {
    showStatus(status, tr("Save the endpoint and key before loading suggestions."));
    return;
  }
  const query = new URLSearchParams({ provider: block.dataset.provider, svc: service });
  button.disabled = true;
  showStatus(status, tr("Loading…"));
  try {
    const { models, error } = await requestJSON("/api/models/list?" + query);
    input.list.replaceChildren(...models.map((model) => {
      const option = document.createElement("option");
      option.value = model;
      return option;
    }));
    showStatus(status, error || tr(models.length ? "Suggestions loaded. Open the model field." : "No suggestions. Enter a model ID."), !!error);
  } catch (error) {
    showStatus(status, error.message, true);
  } finally {
    button.disabled = false;
  }
});

settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (saving || !updates.size) return;
  const sent = new Map(updates);
  save.disabled = saving = true;
  showStatus(saveStatus, tr("Saving…"));
  try {
    await requestJSON("/api/settings", jsonOptions("POST", { settings: Object.fromEntries(sent) }));
    for (const [key, value] of sent) {
      if (updates.get(key) !== value) continue;
      updates.delete(key);
      const input = settingsForm.elements[key];
      if (input instanceof HTMLInputElement && input.hasAttribute("data-secret")) {
        input.value = "";
        input.placeholder = value ? "••••" : tr("API key");
        settingsForm.querySelector('[data-clear-key="' + key + '"]').hidden = !value;
      }
    }
    showStatus(saveStatus, tr(updates.size ? "Unsaved changes" : "Saved successfully."));
    if (sent.has("ui_language") && !updates.size) location.reload();
  } catch (error) {
    showStatus(saveStatus, error.message, true);
  } finally {
    saving = false;
    save.disabled = !updates.size;
  }
});
window.addEventListener("beforeunload", (event) => {
  if (updates.size) event.preventDefault();
});

const detectMicrophones = document.getElementById("detect-microphones");
detectMicrophones.addEventListener("click", async () => {
  const status = document.getElementById("mic-stage");
  const select = document.getElementById("mic-select");
  detectMicrophones.disabled = true;
  let stream;
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error();
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const devices = (await navigator.mediaDevices.enumerateDevices())
      .filter((device) => device.kind === "audioinput" && device.deviceId);
    const current = select.value;
    select.replaceChildren(new Option(tr("Default microphone"), ""));
    for (const [index, device] of devices.entries()) {
      select.add(new Option(device.label || tr("Microphone") + " " + (index + 1), device.deviceId));
    }
    if (current && !devices.some((device) => device.deviceId === current)) {
      select.add(new Option(tr("Saved microphone"), current));
    }
    select.value = current;
    showStatus(status, devices.length ? "" : tr("No microphones detected."));
  } catch {
    showStatus(status, tr("Could not open the microphone."), true);
  } finally {
    stream?.getTracks().forEach((track) => track.stop());
    detectMicrophones.disabled = false;
  }
});

const models = document.querySelector(".models-details");
const modelList = document.getElementById("models-list");
const modelStage = document.getElementById("models-stage");
let loaded = false;
let modelBusy = false;

async function modelAction(path, payload, isInstall, button) {
  if (modelBusy || (!isInstall && !confirm(tr("Delete this model?")))) return;
  button.disabled = modelBusy = true;
  showStatus(modelStage, tr("Working…"));
  try {
    if (isInstall) {
      await startJob(path, jsonOptions("POST", payload), (message) => showStatus(modelStage, message));
      showStatus(modelStage, tr("Model installed."));
    } else {
      await requestJSON(path, jsonOptions("POST", payload));
      showStatus(modelStage, tr("Model deleted."));
    }
    await loadModels(false);
  } catch (error) {
    showStatus(modelStage, error.message, true);
  } finally {
    button.disabled = modelBusy = false;
  }
}

function addGroup(title, items, kind, repo = "") {
  if (!items.length) return;
  const group = document.createElement("details");
  group.className = "model-group";
  group.open = kind !== "llm";
  const summary = document.createElement("summary");
  summary.textContent = title + " (" + items.length + ")";
  group.append(summary);
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "model-row";
    const name = document.createElement("span");
    const suffix = item.installed ? " · " + tr("Installed") : item.system ? " · " + tr("System") : "";
    const size = item.size ? " · " + (item.size / 1024 ** 2).toFixed(0) + " MiB" : "";
    name.textContent = item.name + size + suffix + (item.version ? " " + item.version : "");
    row.append(name);
    if (!item.installed && !item.system || item.installed && kind !== "program") {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-sm";
      button.textContent = tr(item.installed ? "Delete" : "Install");
      button.addEventListener("click", () => item.installed
        ? modelAction("/api/models/delete", { path: item.path }, false, button)
        : modelAction("/api/models/install", { kind, repo, name: item.name }, true, button));
      row.append(button);
    }
    group.append(row);
  }
  modelList.append(group);
}

async function loadModels(showLoading = true) {
  if (showLoading) showStatus(modelStage, tr("Loading…"));
  try {
    const data = await requestJSON("/api/models");
    modelList.replaceChildren();
    addGroup(tr("Programs"), data.programs, "program");
    addGroup(tr("Whisper models"), data.whisper_models, "whisper");
    for (const repo of data.llm) addGroup(repo.repo, repo.quants, "llm", repo.repo);
    const errors = [data.whisper_error, data.llm_error].filter(Boolean);
    if (errors.length || showLoading) showStatus(modelStage, errors.join(" · "), !!errors.length);
    loaded = true;
  } catch (error) {
    showStatus(modelStage, error.message, true);
  }
}
models.addEventListener("toggle", () => {
  if (models.open && !loaded) loadModels();
});
