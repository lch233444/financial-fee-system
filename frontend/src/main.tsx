import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { DatasetBoundary } from "./Datasets";
import "./styles.css";
import "./styles/workspace.css";
import "./styles/progressive.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <DatasetBoundary><App /></DatasetBoundary>
  </StrictMode>,
);

