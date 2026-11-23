(() => {
  const form = document.querySelector("#profile-form");
  if (!form) return;

  const steps = [...form.querySelectorAll("[data-step]")];
  const progress = form.querySelector(".setup-progress");
  const bar = form.querySelector('[role="progressbar"]');
  const label = form.querySelector("#step-label");
  const back = form.querySelector(".setup-back");
  const next = form.querySelector(".setup-next");
  const save = form.querySelector(".setup-save");
  const cm = form.elements.height_cm;
  const feet = form.elements.height_ft;
  const inches = form.elements.height_in;
  const kg = form.elements.weight_kg;
  const lb = form.elements.weight_lb;
  const ftp = form.elements.ftp;
  const zone2 = form.elements.zone2;
  let current = 0;

  function showStep(index, focus = false) {
    current = index;
    steps.forEach((step, i) => { step.hidden = i !== index; });
    label.textContent = `Step ${index + 1} of ${steps.length}`;
    bar.setAttribute("aria-valuenow", index + 1);
    bar.firstElementChild.style.width = `${(index + 1) / steps.length * 100}%`;
    back.hidden = index === 0;
    next.hidden = index === steps.length - 1;
    save.hidden = index !== steps.length - 1;
    if (index === steps.length - 1) updateReview();
    if (focus) steps[index].querySelector("h3").focus();
  }

  function validateChoices() {
    cm.setCustomValidity(!cm.value && !feet.value && !inches.value ? "Enter your height in centimeters or feet." : "");
    const height = (Number(feet.value) * 12 + Number(inches.value || 0)) * 2.54;
    feet.setCustomValidity(inches.value && !feet.value ? "Enter feet along with inches."
      : feet.value && (height < 50 || height > 250) ? "Height must be between 50 and 250 cm after conversion." : "");
    inches.setCustomValidity(inches.value && Number(inches.value) >= 12 ? "Inches must be less than 12." : "");
    kg.setCustomValidity(!kg.value && !lb.value ? "Enter your weight in kilograms or pounds." : "");
    const converted = Number(lb.value) * 0.45359237;
    lb.setCustomValidity(lb.value && (converted < 20 || converted > 350)
      ? "Weight must be between 20 and 350 kg after conversion." : "");
    ftp.setCustomValidity(!ftp.value && !zone2.value ? "Enter FTP or a representative zone 2 power." : "");
  }

  function validStep(index) {
    validateChoices();
    const invalid = [...steps[index].querySelectorAll("input")].find(input => !input.validity.valid);
    if (invalid) {
      showStep(index);
      invalid.reportValidity();
      return false;
    }
    return true;
  }

  function updateReview() {
    const height = feet.value
      ? `${((Number(feet.value) * 12 + Number(inches.value || 0)) * 2.54).toFixed(2)} cm (from ${feet.value} ft ${inches.value || 0} in)`
      : `${cm.value} cm`;
    const weight = lb.value ? `${(Number(lb.value) * 0.45359237).toFixed(2)} kg (from ${lb.value} lb)` : `${kg.value} kg`;
    const power = ftp.value ? `${ftp.value} W FTP` : `${zone2.value} W zone 2 (FTP estimated)`;
    const items = [["HEIGHT", height], ["WEIGHT", weight],
      ["RIDE TIME", `${form.elements.hours_per_week.value} hours / week`], ["POWER", power]];
    const review = form.querySelector("#profile-review");
    review.replaceChildren();
    for (const [title, value] of items) {
      const item = document.createElement("div");
      const heading = document.createElement("span");
      const text = document.createElement("strong");
      heading.textContent = title;
      text.textContent = value;
      item.append(heading, text);
      review.append(item);
    }
  }

  function updateTips() {
    validateChoices();
    const heightTip = form.querySelector("#height-preview");
    heightTip.hidden = !feet.value || !feet.validity.valid || !inches.validity.valid;
    if (!heightTip.hidden) {
      heightTip.textContent = `${feet.value} ft ${inches.value || 0} in converts to ${((Number(feet.value) * 12 + Number(inches.value || 0)) * 2.54).toFixed(2)} cm.`;
    }
    const weightTip = form.querySelector("#weight-preview");
    weightTip.hidden = !lb.value || !lb.validity.valid;
    if (!weightTip.hidden) weightTip.textContent = `${lb.value} lb converts to ${(Number(lb.value) * 0.45359237).toFixed(2)} kg.`;
    const powerTip = form.querySelector("#power-preview");
    powerTip.hidden = !(ftp.value && ftp.validity.valid) && !(zone2.value && zone2.validity.valid && !ftp.value);
    if (!powerTip.hidden) {
      powerTip.textContent = ftp.value
        ? `Your zones will be based on ${ftp.value} W FTP.`
        : `Your estimated FTP will be about ${Math.round(Number(zone2.value) / 0.655)} W. A measured FTP is more reliable.`;
    }
  }

  next.addEventListener("click", () => { if (validStep(current)) showStep(current + 1, true); });
  back.addEventListener("click", () => showStep(current - 1, true));
  form.addEventListener("input", updateTips);
  form.addEventListener("submit", event => {
    if (current !== steps.length - 1) {
      event.preventDefault();
      if (validStep(current)) showStep(current + 1, true);
      return;
    }
    for (let i = 0; i < steps.length; i++) {
      if (!validStep(i)) {
        event.preventDefault();
        return;
      }
    }
  });
  form.noValidate = true;
  progress.hidden = false;
  updateTips();
  showStep(0);

  const eventForm = document.querySelector("#event-form");
  const eventPreview = document.querySelector("#event-preview");
  function updateEvent() {
    const eventDate = eventForm.elements.event_date.value;
    const days = (Date.parse(`${eventDate}T00:00:00Z`) - Date.parse(`${eventForm.dataset.today}T00:00:00Z`)) / 86400000;
    eventPreview.hidden = !eventDate || !Number.isFinite(days);
    if (!eventPreview.hidden) {
      eventPreview.textContent = days > 0
        ? `${days} day${days === 1 ? "" : "s"} to your event. Add it, then generate your plan.`
        : "Choose a future date to build a plan.";
    }
  }
  eventForm.addEventListener("input", updateEvent);

  const rideForm = document.querySelector("#ride-form");
  const ridePreview = document.querySelector("#ride-preview");
  const durationButtons = rideForm.querySelector(".quick-durations");
  durationButtons.hidden = false;
  function updateRide() {
    const duration = rideForm.elements.duration_minutes;
    ridePreview.hidden = !duration.value || !duration.validity.valid;
    if (!ridePreview.hidden) {
      ridePreview.textContent = `${duration.value} minutes · ${rideForm.elements.status.value === "planned" ? "on the road ahead" : "another ride in the books"}.`;
    }
    durationButtons.querySelectorAll("button").forEach(button => {
      button.setAttribute("aria-pressed", String(button.dataset.duration === duration.value));
    });
  }
  durationButtons.addEventListener("click", event => {
    const button = event.target.closest("button[data-duration]");
    if (!button) return;
    rideForm.elements.duration_minutes.value = button.dataset.duration;
    updateRide();
  });
  rideForm.addEventListener("input", updateRide);
  rideForm.addEventListener("change", updateRide);
  updateRide();
})();
