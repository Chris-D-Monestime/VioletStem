/* VioletStem frontend: upload -> poll -> preview -> download. Honest indeterminate progress. */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var drop = $("drop"), fileInput = $("fileInput"), browseBtn = $("browseBtn");
  var fileInfo = $("fileInfo"), fName = $("fName"), fMeta = $("fMeta"), preview = $("preview");
  var removeBtn = $("removeBtn"), goBtn = $("goBtn"), uploadErr = $("uploadErr");
  var proc = $("proc"), stageLabel = $("stageLabel"), stageMsg = $("stageMsg"), barFill = $("barFill");
  var cancelBtn = $("cancelBtn"), procErr = $("procErr"), retryBtn = $("retryBtn");
  var results = $("results"), resMeta = $("resMeta");
  var vocalsAudio = $("vocalsAudio"), instrAudio = $("instrAudio");
  var vocalsMeta = $("vocalsMeta"), instrMeta = $("instrMeta");
  var vocalsDl = $("vocalsDl"), instrDl = $("instrDl"), zipDl = $("zipDl");
  var againBtn = $("againBtn"), syncBox = $("syncBox"), enginePill = $("enginePill");
  var menuBtn = $("menuBtn"), menu = $("menu");

  var selected = null, jobId = null, pollTimer = null, submitting = false, lastBlobUrl = null;

  menuBtn.addEventListener("click", function () {
    var open = menu.classList.toggle("open");
    menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
  });
  menu.querySelectorAll("a").forEach(function (a) {
    a.addEventListener("click", function () { menu.classList.remove("open"); });
  });

  fetch("api/health").then(function (r) { return r.json(); }).then(function (h) {
    var e = h.engine || {};
    enginePill.textContent = "Engine: " + (e.active || "unknown") + " (" + (e.kind || "?") + ")";
    var lim = $("limitsLine");
    if (lim) lim.textContent = "MP3 only · max " + (h.max_upload_mb || 50) + " MB · files processed temporarily";
  }).catch(function () { enginePill.textContent = "Engine status unavailable"; });

  function fmtSize(b) {
    if (b == null) return "";
    if (b < 1024) return b + " B";
    if (b < 1048576) return (b / 1024).toFixed(1) + " KB";
    return (b / 1048576).toFixed(1) + " MB";
  }
  function fmtDur(s) {
    if (s == null || isNaN(s)) return "";
    s = Math.round(s);
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }
  function showErr(el, msg) {
    if (!msg) { el.hidden = true; el.textContent = ""; return; }
    el.hidden = false; el.textContent = msg;
  }

  function pickFile(f) {
    showErr(uploadErr, "");
    if (!f) return;
    var ext = (f.name.split(".").pop() || "").toLowerCase();
    var typeOK = (f.type === "audio/mpeg" || f.type === "audio/mp3" || f.type === "");
    if (ext !== "mp3" || (f.type && !typeOK)) {
      showErr(uploadErr, "Unsupported format. Please choose an MP3 file.");
      return;
    }
    if (f.size === 0) { showErr(uploadErr, "Empty file. Please choose a valid MP3."); return; }
    selected = f;
    fName.textContent = f.name + "  ";
    fMeta.textContent = fmtSize(f.size);
    if (lastBlobUrl) URL.revokeObjectURL(lastBlobUrl);
    lastBlobUrl = URL.createObjectURL(f);
    preview.src = lastBlobUrl;
    preview.onloadedmetadata = function () {
      if (isFinite(preview.duration)) fMeta.textContent = fmtSize(f.size) + " · " + fmtDur(preview.duration);
    };
    fileInfo.hidden = false;
    goBtn.disabled = false;
    drop.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  drop.addEventListener("click", function (e) {
    if (e.target === browseBtn) return;
    fileInput.click();
  });
  browseBtn.addEventListener("click", function (e) { e.stopPropagation(); fileInput.click(); });
  drop.addEventListener("keydown", function (e) {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
  });
  fileInput.addEventListener("change", function () { pickFile(fileInput.files[0]); });
  ["dragover", "dragenter"].forEach(function (ev) {
    drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("over"); });
  });
  ["dragleave", "drop"].forEach(function (ev) {
    drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("over"); });
  });
  drop.addEventListener("drop", function (e) {
    var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    pickFile(f);
  });
  removeBtn.addEventListener("click", function () {
    selected = null; fileInput.value = "";
    fileInfo.hidden = true; preview.removeAttribute("src"); preview.load();
  });

  var STAGE_PCT = { queued: 4, uploading: 12, preparing: 25, separating: 60, finishing: 88, completed: 100 };
  function renderStage(stage, msg) {
    stageLabel.textContent = { queued: "Queued…", uploading: "Uploading audio…", preparing: "Preparing audio…", separating: "Separating vocals and instrumental…", finishing: "Processing results…", completed: "Completed successfully.", failed: "Failed.", cancelled: "Cancelled." }[stage] || stage;
    stageMsg.textContent = msg || "";
    barFill.classList.add("indet"); // honest indeterminate: backend gives stages, not %
    barFill.style.width = (STAGE_PCT[stage] || 10) + "%";
    barFill.parentElement.setAttribute("aria-valuenow", String(STAGE_PCT[stage] || 10));
  }

  goBtn.addEventListener("click", function () {
    if (!selected) { showErr(uploadErr, "No file selected. Choose an MP3 first."); return; }
    if (submitting) return;
    submitting = true; goBtn.disabled = true;
    showErr(procErr, ""); retryBtn.hidden = true;
    fileInfo.hidden = true; proc.hidden = false; results.hidden = true;
    renderStage("uploading", "Uploading " + selected.name + "…");
    var fd = new FormData();
    fd.append("file", selected, selected.name);
    var xhr = new XMLHttpRequest();
    xhr.open("POST", "api/separations");
    xhr.onload = function () {
      if (xhr.status === 201) {
        var job = JSON.parse(xhr.responseText);
        jobId = job.job_id;
        renderStage("preparing", "Upload complete. Preparing audio…");
        poll();
      } else {
        var msg = "Upload failed.";
        try { msg = JSON.parse(xhr.responseText).detail || msg; } catch (e) {}
        fail(msg); submitting = false; goBtn.disabled = false;
      }
    };
    xhr.onerror = function () { fail("Network disconnected during upload. Check connection and retry."); submitting = false; goBtn.disabled = false; };
    xhr.send(fd);
  });

  function poll() {
    clearTimeout(pollTimer);
    fetch("api/separations/" + encodeURIComponent(jobId)).then(function (r) {
      if (!r.ok) throw new Error("Job not found or expired.");
      return r.json();
    }).then(function (j) {
      renderStage(j.stage, j.message + (j.engine ? "  ·  engine: " + j.engine : ""));
      if (j.status === "completed") { submitting = false; done(j); }
      else if (j.status === "failed") { submitting = false; fail(j.message || j.error || "Processing failed."); }
      else if (j.status === "cancelled") { submitting = false; fail("Job cancelled."); }
      else { pollTimer = setTimeout(poll, 1500); }
    }).catch(function (e) { submitting = false; fail(String(e.message || e)); });
  }

  function fail(msg) {
    showErr(procErr, msg + " You can retry without reloading the page.");
    retryBtn.hidden = false;
    stageLabel.textContent = "Failed.";
  }
  retryBtn.addEventListener("click", function () {
    if (!selected) return;
    procErr.hidden = true; retryBtn.hidden = true; submitting = false; goBtn.disabled = false;
    fileInfo.hidden = false; proc.hidden = true;
  });
  cancelBtn.addEventListener("click", function () {
    if (!jobId) return;
    fetch("api/separations/" + encodeURIComponent(jobId), { method: "DELETE" });
    stageMsg.textContent = "Cancellation requested…";
  });

  function done(j) {
    renderStage("completed", j.message);
    results.hidden = false;
    resMeta.textContent = (j.original_name || "") + (j.engine ? " · engine: " + j.engine : "") + " · files auto-delete after retention period.";
    vocalsAudio.src = j.vocals.url; instrAudio.src = j.instrumental.url;
    vocalsDl.href = j.vocals.url; instrDl.href = j.instrumental.url; zipDl.href = j.zip_url;
    vocalsDl.download = "vocals.wav"; instrDl.download = "instrumental.wav";
    vocalsMeta.textContent = fmtDur(j.vocals.duration) + " · " + fmtSize(j.vocals.size);
    instrMeta.textContent = fmtDur(j.instrumental.duration) + " · " + fmtSize(j.instrumental.size);
    results.scrollIntoView({ behavior: "smooth" });
  }
  againBtn.addEventListener("click", function () {
    results.hidden = true; proc.hidden = true; fileInfo.hidden = true;
    selected = null; jobId = null; fileInput.value = "";
    document.getElementById("upload").scrollIntoView({ behavior: "smooth" });
  });

  // optional sync: keep players' positions aligned
  var syncing = false;
  function syncFrom(src, dst) {
    if (!syncBox.checked || syncing) return;
    syncing = true;
    try { if (Math.abs(dst.currentTime - src.currentTime) > 0.3) dst.currentTime = src.currentTime; } catch (e) {}
    syncing = false;
  }
  vocalsAudio.addEventListener("timeupdate", function () { syncFrom(vocalsAudio, instrAudio); });
  instrAudio.addEventListener("timeupdate", function () { syncFrom(instrAudio, vocalsAudio); });
  vocalsAudio.addEventListener("play", function () { if (syncBox.checked) instrAudio.play().catch(function () {}); });
  vocalsAudio.addEventListener("pause", function () { if (syncBox.checked) instrAudio.pause(); });
})();
