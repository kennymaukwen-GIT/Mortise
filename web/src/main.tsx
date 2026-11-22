import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// Self-hosted fonts (SIL Open Font License); nothing is fetched from a font service.
import "@fontsource/barlow/latin-400.css";
import "@fontsource/barlow/latin-500.css";
import "@fontsource/barlow/latin-600.css";
import "@fontsource/barlow/latin-700.css";
import "@fontsource-variable/noto-sans-tc/index.css";
import "./styles/app.css";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
