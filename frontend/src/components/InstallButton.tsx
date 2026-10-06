import { useState, useSyncExternalStore } from "react";
import { installOption, onInstallChange, promptInstall } from "../install";

/**
 * "Install app": one click once the browser offers it (Chrome, Edge, Samsung Internet). Before
 * that on Android, and always on iPhone/iPad, it shows the menu steps instead. Hidden where
 * installing isn't possible, or once installed.
 */
export function InstallButton({ className = "" }: { className?: string }) {
  const option = useSyncExternalStore(onInstallChange, () => installOption());
  const [showTip, setShowTip] = useState(false);
  if (!option) return null;

  return (
    <div className="install">
      <button
        type="button"
        className={`install-button ${className}`.trim()}
        aria-expanded={option !== "prompt" ? showTip : undefined}
        onClick={() => (option === "prompt" ? void promptInstall() : setShowTip((s) => !s))}
      >
        <span aria-hidden="true" className="install-icon">
          ⤓
        </span>
        Install app
      </button>
      {option === "ios" && showTip && (
        <p className="install-tip" role="status">
          Tap <strong>Share</strong> <span aria-hidden="true">(the square with an arrow)</span>, then{" "}
          <strong>Add to Home Screen</strong>.
        </p>
      )}
      {option === "android" && showTip && (
        <p className="install-tip" role="status">
          Open your browser's menu <span aria-hidden="true">(⋮ or ≡)</span>, then tap <strong>Install app</strong> or{" "}
          <strong>Add to Home screen</strong>.
        </p>
      )}
    </div>
  );
}
