/** Translucent cover with a 2D satellite orbiting a 2D Moon. It sits over a view while that view calculates or renders.
 * Presentation only: it reports nothing about progress beyond the text the caller gives it. */
export class LoadingCover {
 readonly el: HTMLElement;
 private shownAt = 0;
 private hideTimer = 0;

 constructor(parent: HTMLElement, className = "") {
  this.el = document.createElement("div");
  this.el.className = `orbit-loader ${className}`.trim();
  this.el.hidden = true;
  this.el.setAttribute("role", "status");
  this.el.setAttribute("aria-live", "polite");
  this.el.innerHTML = `
   <div class="orbit-loader-card">
    <svg class="orbit-loader-art" viewBox="0 0 200 200" aria-hidden="true">
     <circle class="ol-path" cx="100" cy="100" r="72"/>
     <circle class="ol-moon" cx="100" cy="100" r="42"/>
     <g class="ol-craters"><circle cx="86" cy="88" r="8"/><circle cx="116" cy="110" r="10"/><circle cx="108" cy="78" r="4"/><circle cx="84" cy="117" r="5"/></g>
     <g class="ol-orbit">
      <line class="ol-beam" x1="100" y1="40" x2="100" y2="58"/>
      <g transform="translate(100 28)">
       <rect class="ol-panel" x="-26" y="-5" width="16" height="10"/>
       <rect class="ol-panel" x="10" y="-5" width="16" height="10"/>
       <rect class="ol-strut" x="-10" y="-1" width="20" height="2"/>
       <rect class="ol-body" x="-6" y="-7" width="12" height="14"/>
      </g>
     </g>
    </svg>
    <strong data-title>Calculating</strong>
    <span data-detail></span>
   </div>`;
  parent.append(this.el);
 }

 get visible() { return !this.el.hidden && !this.el.classList.contains("leaving"); }

 show(title: string, detail = "") {
  clearTimeout(this.hideTimer);
  this.el.querySelector("[data-title]")!.textContent = title;
  this.el.querySelector("[data-detail]")!.textContent = detail;
  if (this.el.hidden || this.el.classList.contains("leaving")) this.shownAt = performance.now();
  this.el.classList.remove("leaving");
  this.el.hidden = false;
 }

 /** Fade out. A cover shown only briefly stays up long enough to read, so it never flashes. */
 hide() {
  if (this.el.hidden) return;
  clearTimeout(this.hideTimer);
  const wait = Math.max(0, 450 - (performance.now() - this.shownAt));
  this.hideTimer = window.setTimeout(() => {
   this.el.classList.add("leaving");
   this.hideTimer = window.setTimeout(() => { this.el.hidden = true; this.el.classList.remove("leaving"); }, 320);
  }, wait);
 }

 /** Stay up and say what went wrong, instead of vanishing and leaving an empty view. */
 fail(title: string, detail: string, action?: {label: string; run: () => void}) {
  this.show(title, detail);
  this.el.classList.add("failed");
  this.el.querySelector("[data-fail-action]")?.remove();
  if (action) {
   const button = document.createElement("button");
   button.dataset.failAction = ""; button.className = "orbit-loader-action"; button.textContent = action.label; button.onclick = action.run;
   this.el.querySelector(".orbit-loader-card")!.append(button);
   button.focus();
  }
 }

 dispose() { clearTimeout(this.hideTimer); this.el.remove(); }
}
