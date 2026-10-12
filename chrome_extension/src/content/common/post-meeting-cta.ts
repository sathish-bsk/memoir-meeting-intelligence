import { WEB_BASE } from "../../shared/constants";
import styles from "./styles.css";

/** Shows a post-meeting CTA card when the meeting ends */
export class PostMeetingCTA {
  private root: HTMLElement;
  private shadow: ShadowRoot;

  constructor(meetingId: string) {
    this.root = document.createElement("div");
    this.root.id = "memoir-post-cta-root";
    this.shadow = this.root.attachShadow({ mode: "closed" });

    const style = document.createElement("style");
    style.textContent = styles;
    this.shadow.appendChild(style);

    const card = document.createElement("div");
    card.className = "memoir-post-cta";
    card.innerHTML = `
      <div class="memoir-post-cta-header">
        <div class="memoir-post-cta-icon">M</div>
        <h3 class="memoir-post-cta-title">Your transcript is getting ready</h3>
      </div>
      <p class="memoir-post-cta-body">Your meeting has been recorded and is being processed by Memoir.</p>
      <div>
        <a class="memoir-post-cta-link" href="${WEB_BASE}/meetings/${meetingId}" target="_blank" rel="noopener">
          View in Memoir &rarr;
        </a>
        <button class="memoir-post-cta-dismiss">Dismiss</button>
      </div>
    `;
    this.shadow.appendChild(card);

    card.querySelector(".memoir-post-cta-dismiss")?.addEventListener("click", () => {
      this.destroy();
    });

    document.body.appendChild(this.root);
  }

  destroy() {
    this.root.remove();
  }
}
