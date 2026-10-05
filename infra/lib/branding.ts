/**
 * OneBill's look for Cognito's managed login page (sign-in, sign-up, password reset).
 * Only the values that differ from Cognito's defaults: Cognito merges these over its own
 * (the format was read from `describe-managed-login-branding-by-client --return-merged-resources`).
 * Colors are RRGGBBAA hex, matching the app's theme in frontend/src/index.css.
 */
const accent = "d9774fff";
const accentStrong = "c2603aff";
const accentSoft = "fbe8ddff";

export const loginBrandingSettings = {
  components: {
    primaryButton: {
      lightMode: {
        defaults: { backgroundColor: accent, textColor: "ffffffff" },
        hover: { backgroundColor: accentStrong, textColor: "ffffffff" },
        active: { backgroundColor: accentStrong, textColor: "ffffffff" },
      },
    },
    pageBackground: { image: { enabled: false }, lightMode: { color: "fbf6efff" } },
    form: {
      borderRadius: 16.0,
      lightMode: { backgroundColor: "ffffffff", borderColor: "ecdfd2ff" },
      logo: { enabled: true, location: "CENTER", position: "TOP", formInclusion: "IN" },
    },
    pageText: {
      lightMode: { headingColor: "2d2520ff", bodyColor: "5c4f45ff", descriptionColor: "8a7d72ff" },
    },
    idpButton: {
      standard: {
        lightMode: {
          defaults: { backgroundColor: "ffffffff", borderColor: "dadce0ff", textColor: "1f1f1fff" },
          hover: { backgroundColor: accentSoft, borderColor: accentStrong, textColor: "2d2520ff" },
          active: { backgroundColor: accentSoft, borderColor: accentStrong, textColor: "2d2520ff" },
        },
      },
    },
  },
  componentClasses: {
    link: { lightMode: { defaults: { textColor: accentStrong }, hover: { textColor: "9a4a2cff" } } },
    focusState: { lightMode: { borderColor: accent } },
    input: { borderRadius: 10.0, lightMode: { defaults: { backgroundColor: "ffffffff", borderColor: "d9c9b9ff" } } },
    buttons: { borderRadius: 12.0 },
  },
  categories: {
    global: { colorSchemeMode: "LIGHT" },
  },
};

/** The OneBill logo (mark + name), shown at the top of the login form. Cognito requires a
 * width:height ratio between 1:1 and 4:1 (this is 3.75:1). */
const LOGO_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 150 40" width="150" height="40">
<rect x="2" y="3" width="34" height="34" rx="10" fill="#d9774f"/>
<path d="M12 12h14v16l-2.3-1.6-2.4 1.6-2.3-1.6-2.4 1.6-2.3-1.6L12 28z" fill="none" stroke="#fff" stroke-width="2" stroke-linejoin="round"/>
<path d="M15.5 17.5h7M15.5 21.5h7" stroke="#fff" stroke-width="2" stroke-linecap="round"/>
<text x="46" y="28" font-family="-apple-system, system-ui, Segoe UI, Roboto, Helvetica, Arial, sans-serif" font-size="22" font-weight="800" letter-spacing="-0.5" fill="#2d2520">OneBill</text>
</svg>`;

export const loginBrandingAssets = [
  {
    category: "FORM_LOGO",
    colorMode: "LIGHT",
    extension: "SVG",
    bytes: Buffer.from(LOGO_SVG).toString("base64"),
  },
];
