"use strict";

(() => {
  const capture = document.getElementById("capture");
  if (!capture) return;
  const record = document.getElementById("record");
  const stop = document.getElementById("stop");
  const pause = document.getElementById("pause");
  const cancel = document.getElementById("cancel");
  const retry = document.getElementById("retry-recording");
  const stage = document.getElementById("stage");
  const errorBox = document.getElementById("record-error");
  const timer = document.getElementById("timer");
  const mode = document.getElementById("record-mode");
  const canvas = document.getElementById("meter");
  const painter = canvas.getContext("2d");
  const limit = Number(capture.dataset.limit) * 1000;
  let state = "idle";
  let stream, context, recorder, analyser, samples, tick, frame;
  let elapsed = 0;
  let started = 0;
  let lastRecording;
  let leftPage = false;

  function setState(value, message) {
    state = value;
    capture.dataset.state = value;
    const active = value === "recording" || value === "paused";
    record.hidden = active;
    record.disabled = mode.disabled = value !== "idle";
    stop.hidden = pause.hidden = cancel.hidden = !active;
    pause.textContent = tr(value === "paused" ? "Resume" : "Pause");
    pause.setAttribute("aria-pressed", String(value === "paused"));
    showStatus(stage, message);
  }

  function report(message = "") {
    errorBox.textContent = message;
    errorBox.hidden = !message;
  }

  function duration() {
    return elapsed + (state === "recording" ? performance.now() - started : 0);
  }

  function updateTime() {
    const total = Math.floor(duration() / 1000);
    timer.textContent = [Math.floor(total / 60), total % 60]
      .map((number) => String(number).padStart(2, "0")).join(":");
    if (state === "recording" && limit > 0 && duration() >= limit) finish();
  }

  function paint() {
    if (!painter) return;
    const { width, height } = canvas;
    const color = getComputedStyle(canvas).color;
    painter.clearRect(0, 0, width, height);
    painter.strokeStyle = color;
    painter.globalAlpha = 0.25;
    painter.beginPath();
    for (let index = 0; index < 13; index++) {
      const x = index * (width / 12);
      painter.moveTo(x, height * 0.9);
      painter.lineTo(x, height);
    }
    painter.stroke();
    painter.globalAlpha = 1;
    painter.lineWidth = 2;
    painter.beginPath();
    if (state === "recording" && analyser) {
      analyser.getByteTimeDomainData(samples);
      for (let index = 0; index < samples.length; index++) {
        const x = index * width / (samples.length - 1);
        const y = height * (0.48 + (samples[index] - 128) / 250);
        if (index === 0) painter.moveTo(x, y);
        else painter.lineTo(x, y);
      }
      frame = requestAnimationFrame(paint);
    } else {
      painter.moveTo(0, height * 0.48);
      painter.lineTo(width, height * 0.48);
    }
    painter.stroke();
    painter.lineWidth = 1;
  }

  function release() {
    stream?.getTracks().forEach((track) => track.stop());
    stream = null;
    if (context && context.state !== "closed") context.close().catch(() => {});
    context = analyser = null;
    clearInterval(tick);
    cancelAnimationFrame(frame);
    paint();
  }

  function addRecent({ id, data }) {
    if (!data.text) return;
    const list = document.getElementById("recent-list");
    const row = document.createElement("li");
    const body = document.createElement("div");
    const preview = document.createElement("p");
    preview.id = "recent-" + id;
    preview.textContent = data.text;
    const meta = document.createElement("small");
    meta.textContent = [data.ts, data.model].filter(Boolean).join(" · ");
    body.append(preview, meta);
    const copy = document.createElement("button");
    copy.className = "btn btn-sm btn-ghost";
    copy.dataset.copy = "";
    copy.dataset.target = "#" + preview.id;
    copy.textContent = tr("Copy");
    row.append(body, copy);
    list.prepend(row);
    if (list.children.length > 4) list.lastElementChild.remove();
    list.hidden = false;
    document.getElementById("empty-recent").hidden = true;
  }

  async function send(blob, submitMode) {
    report();
    retry.hidden = true;
    setState("processing", tr("Uploading…"));
    const ext = { "audio/mp4": "m4a", "audio/ogg": "ogg", "audio/wav": "wav", "audio/webm": "webm" };
    const body = new FormData();
    body.append("audio", blob, "recording." + (ext[blob.type.split(";")[0]] || "audio"));
    body.append("ask", String(submitMode === "ask"));
    try {
      const result = await startJob("/api/dictate", { method: "POST", body },
        (message) => showStatus(stage, message));
      displayTranscript(result);
      addRecent(result);
      lastRecording = null;
    } catch (error) {
      lastRecording = { blob, mode: submitMode };
      retry.hidden = false;
      report(error.message);
    } finally {
      setState("idle", tr("Ready"));
    }
  }

  async function start() {
    if (state !== "idle") return;
    report();
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      report(tr("Microphone access requires HTTPS or localhost and a supported browser."));
      return;
    }
    elapsed = 0;
    updateTime();
    setState("requesting", tr("Waiting for microphone access…"));
    const device = capture.dataset.microphone;
    const submitMode = mode.value;
    try {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: device ? { deviceId: { exact: device } } : true,
        });
      } catch (error) {
        if (!device || !["NotFoundError", "OverconstrainedError"].includes(error.name)) throw error;
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        report(tr("The selected microphone is unavailable. Using the default."));
      }
      if (leftPage) {
        release();
        return;
      }
      const chunks = [];
      recorder = new MediaRecorder(stream);
      recorder.ondataavailable = ({ data }) => { if (data.size) chunks.push(data); };
      recorder.onstop = () => {
        if (state !== "processing") {
          setState("processing", tr("Working…"));
        }
        const blob = new Blob(chunks, { type: recorder.mimeType || chunks[0]?.type || "audio/webm" });
        recorder = null;
        release();
        if (blob.size) send(blob, submitMode);
        else {
          report(tr("No audio was recorded."));
          setState("idle", tr("Ready"));
        }
      };
      recorder.onerror = () => {
        discard();
        report(tr("Recording stopped because the microphone became unavailable."));
      };
      recorder.start();
      try {
        if (window.AudioContext) {
          context = new AudioContext();
          context.resume().catch(() => {});
          analyser = context.createAnalyser();
          analyser.fftSize = 1024;
          samples = new Uint8Array(analyser.fftSize);
          context.createMediaStreamSource(stream).connect(analyser);
        }
      } catch {
        analyser = null;
      }
      retry.hidden = true;
      lastRecording = null;
      started = performance.now();
      setState("recording", tr("Recording…"));
      paint();
      tick = setInterval(updateTime, 150);
    } catch (error) {
      release();
      recorder = null;
      setState("idle", tr("Ready"));
      report(tr(error.name === "NotAllowedError" ? "Allow microphone access in your browser to record." : "Could not open the microphone."));
    }
  }

  function finish() {
    if (state !== "recording" && state !== "paused") return;
    elapsed = duration();
    setState("processing", tr("Working…"));
    updateTime();
    if (recorder?.state !== "inactive") recorder?.stop();
  }

  function discard() {
    if (recorder) {
      recorder.onstop = null;
      if (recorder.state !== "inactive") recorder.stop();
      recorder = null;
    }
    elapsed = 0;
    setState("idle", tr("Ready"));
    release();
    updateTime();
  }

  record.addEventListener("click", start);
  stop.addEventListener("click", finish);
  pause.addEventListener("click", () => {
    if (state === "recording") {
      elapsed = duration();
      recorder.pause();
      cancelAnimationFrame(frame);
      setState("paused", tr("Paused"));
      paint();
    } else if (state === "paused") {
      recorder.resume();
      started = performance.now();
      setState("recording", tr("Recording…"));
      paint();
    }
  });
  cancel.addEventListener("click", () => { discard(); report(); });
  retry.addEventListener("click", (event) => {
    event.preventDefault();
    if (state === "idle" && lastRecording) send(lastRecording.blob, lastRecording.mode);
  });
  document.addEventListener("keydown", (event) => {
    if (event.repeat || event.isComposing || event.defaultPrevented) return;
    if (event.key === "Escape" && (state === "recording" || state === "paused")) {
      event.preventDefault();
      discard();
      report();
      return;
    }
    if (!event.ctrlKey || event.altKey || event.metaKey || event.shiftKey || event.code !== "Space") return;
    if (event.target instanceof Element && event.target.closest("input, textarea, select, [contenteditable=true]")) return;
    event.preventDefault();
    if (state === "idle") start();
    else finish();
  });
  window.addEventListener("pagehide", () => {
    leftPage = true;
    if (state === "recording" || state === "paused") discard();
  });
  window.addEventListener("pageshow", () => { leftPage = false; });
  paint();
})();
