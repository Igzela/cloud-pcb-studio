import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import React from "react";
import { RootCircuit } from "@tscircuit/core";
import {
  circuitJsonToPcbSvg,
  circuitJsonToSchematicSvg,
} from "circuit-to-svg";
import {
  convertCircuitJsonToGerberCommands,
  stringifyGerberCommandLayers,
  convertSoupToExcellonDrillCommands,
  stringifyExcellonDrill,
} from "circuit-json-to-gerber";
import {
  CircuitJsonToKicadPcbConverter,
  CircuitJsonToKicadSchConverter,
} from "circuit-json-to-kicad";

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i++) {
    const key = argv[i];
    if (key.startsWith("--")) {
      args[key.slice(2)] = argv[i + 1];
      i++;
    }
  }
  return args;
}

const args = parseArgs(process.argv);
const inputPath = path.resolve(args.input || "cloud_pcb/layout_route/board1.mjs");
const outDir = path.resolve(args.outdir || "artifacts/board1_tscircuit");
fs.mkdirSync(outDir, { recursive: true });

const mod = await import(pathToFileURL(inputPath).href);
const BoardComponent = mod.default || mod.Board1;
if (!BoardComponent) {
  throw new Error(`No Board component exported from ${inputPath}`);
}

const circuit = new RootCircuit();
circuit.add(React.createElement(BoardComponent));
await circuit.renderUntilSettled();

const circuitJson = circuit.getCircuitJson();

// Write circuit.json
fs.writeFileSync(
  path.join(outDir, "circuit.json"),
  JSON.stringify(circuitJson, null, 2),
  "utf8"
);

// Write SVGs
fs.writeFileSync(
  path.join(outDir, "pcb.svg"),
  circuitJsonToPcbSvg(circuitJson),
  "utf8"
);
fs.writeFileSync(
  path.join(outDir, "schematic.svg"),
  circuitJsonToSchematicSvg(circuitJson),
  "utf8"
);

// Write RS-274X Gerbers
const gerberCommands = convertCircuitJsonToGerberCommands(circuitJson);
const gerberLayers = stringifyGerberCommandLayers(gerberCommands);

const layerFilenameMap = {
  F_Cu: "F_Cu.gbr",
  B_Cu: "B_Cu.gbr",
  In1_Cu: "In1_Cu.gbr",
  In2_Cu: "In2_Cu.gbr",
  F_Mask: "F_Mask.gbr",
  B_Mask: "B_Mask.gbr",
  F_SilkScreen: "F_SilkS.gbr",
  B_SilkScreen: "B_SilkS.gbr",
  F_Paste: "F_Paste.gbr",
  B_Paste: "B_Paste.gbr",
  Edge_Cuts: "Edge_Cuts.gbr",
};

const writtenGerbers = [];
for (const [layerKey, content] of Object.entries(gerberLayers)) {
  const filename = layerFilenameMap[layerKey] || `${layerKey}.gbr`;
  fs.writeFileSync(path.join(outDir, filename), content, "utf8");
  writtenGerbers.push(filename);
}

// Write Excellon Drills (PTH & NPTH)
const pthCommands = convertSoupToExcellonDrillCommands({
  circuitJson,
  is_plated: true,
});
const pthText = stringifyExcellonDrill(pthCommands);
fs.writeFileSync(path.join(outDir, "PTH.drl"), pthText, "utf8");

const npthCommands = convertSoupToExcellonDrillCommands({
  circuitJson,
  is_plated: false,
});
const npthText = stringifyExcellonDrill(npthCommands);
fs.writeFileSync(path.join(outDir, "NPTH.drl"), npthText, "utf8");

// Write KiCad PCB and Schematic
const pcbConverter = new CircuitJsonToKicadPcbConverter(circuitJson, {
  projectName: "board1_tscircuit",
});
pcbConverter.runUntilFinished();
fs.writeFileSync(
  path.join(outDir, "board1.kicad_pcb"),
  pcbConverter.getOutputString(),
  "utf8"
);

const schConverter = new CircuitJsonToKicadSchConverter(circuitJson);
schConverter.runUntilFinished();
fs.writeFileSync(
  path.join(outDir, "board1.kicad_sch"),
  schConverter.getOutputString(),
  "utf8"
);

const errors = circuitJson.filter(
  (el) =>
    el.error_type ||
    (typeof el.type === "string" && el.type.endsWith("_error"))
);
const warnings = circuitJson.filter(
  (el) => typeof el.type === "string" && el.type.endsWith("_warning")
);

const summary = {
  element_count: circuitJson.length,
  error_count: errors.length,
  warning_count: warnings.length,
  errors: errors.map((e) => ({ type: e.type, message: e.message })),
  warnings: warnings.map((w) => ({ type: w.type, message: w.message })),
  gerber_files: writtenGerbers,
  drill_files: ["PTH.drl", "NPTH.drl"],
  pcb_traces: circuitJson.filter((e) => e.type === "pcb_trace").length,
  pcb_vias: circuitJson.filter((e) => e.type === "pcb_via").length,
  pcb_copper_pours: circuitJson.filter((e) => e.type === "pcb_copper_pour").length,
  pcb_keepouts: circuitJson.filter((e) => e.type === "pcb_keepout").length,
};

console.log(JSON.stringify(summary, null, 2));
