// cloud_pcb/layout_route/board1.tsx
import { jsx, jsxs } from "react/jsx-runtime";
var Board1 = () => /* @__PURE__ */ jsxs(
  "board",
  {
    width: "60mm",
    height: "40mm",
    layers: 4,
    material: "fr4",
    thickness: "1.6mm",
    minViaHoleDiameter: "0.3mm",
    minViaPadDiameter: "0.55mm",
    placementDrcChecksDisabled: true,
    children: [
      /* @__PURE__ */ jsx("hole", { diameter: "2.2mm", pcbX: "-26.5mm", pcbY: "17mm" }),
      /* @__PURE__ */ jsx("hole", { diameter: "2.2mm", pcbX: "-26.5mm", pcbY: "-17mm" }),
      /* @__PURE__ */ jsx("hole", { diameter: "2.2mm", pcbX: "26.5mm", pcbY: "17mm" }),
      /* @__PURE__ */ jsx("hole", { diameter: "2.2mm", pcbX: "26.5mm", pcbY: "-17mm" }),
      /* @__PURE__ */ jsx(
        "copperpour",
        {
          name: "GND_IN1",
          layer: "inner1",
          connectsTo: "net.GND",
          boardEdgeMargin: "0.5mm",
          clearance: "0.25mm",
          unbroken: true
        }
      ),
      /* @__PURE__ */ jsx(
        "copperpour",
        {
          name: "GND_IN2",
          layer: "inner2",
          connectsTo: "net.GND",
          boardEdgeMargin: "0.5mm",
          clearance: "0.25mm",
          unbroken: true
        }
      ),
      /* @__PURE__ */ jsx(
        "keepout",
        {
          shape: "rect",
          width: "5mm",
          height: "5mm",
          pcbX: "25mm",
          pcbY: "10mm",
          layers: ["top", "bottom", "inner1", "inner2"]
        }
      ),
      /* @__PURE__ */ jsx(
        "chip",
        {
          name: "J_USB",
          pcbX: "-25.5mm",
          pcbY: "0mm",
          schX: -12,
          schY: 0,
          pinLabels: {
            pin1: "GND1",
            pin2: "VBUS1",
            pin3: "CC1",
            pin4: "DP",
            pin5: "DM",
            pin6: "CC2",
            pin7: "VBUS2",
            pin8: "GND2",
            pin9: "SH1",
            pin10: "SH2",
            pin11: "SH3",
            pin12: "SH4"
          },
          footprint: /* @__PURE__ */ jsxs("footprint", { children: [
            /* @__PURE__ */ jsx("smtpad", { portHints: ["1", "GND1"], shape: "rect", width: "1.6mm", height: "0.9mm", pcbX: "2.8mm", pcbY: "4.0mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["2", "VBUS1"], shape: "rect", width: "1.6mm", height: "1.2mm", pcbX: "2.8mm", pcbY: "2.6mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["3", "CC1"], shape: "rect", width: "1.2mm", height: "0.35mm", pcbX: "2.8mm", pcbY: "1.05mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["4", "DP"], shape: "rect", width: "1.2mm", height: "0.32mm", pcbX: "2.8mm", pcbY: "0.35mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["5", "DM"], shape: "rect", width: "1.2mm", height: "0.32mm", pcbX: "2.8mm", pcbY: "-0.35mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["6", "CC2"], shape: "rect", width: "1.2mm", height: "0.35mm", pcbX: "2.8mm", pcbY: "-1.05mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["7", "VBUS2"], shape: "rect", width: "1.6mm", height: "1.2mm", pcbX: "2.8mm", pcbY: "-2.6mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["8", "GND2"], shape: "rect", width: "1.6mm", height: "0.9mm", pcbX: "2.8mm", pcbY: "-4.0mm" }),
            /* @__PURE__ */ jsx("platedhole", { portHints: ["9", "SH1"], shape: "circle", holeDiameter: "0.9mm", outerDiameter: "1.5mm", pcbX: "-1.5mm", pcbY: "4.8mm" }),
            /* @__PURE__ */ jsx("platedhole", { portHints: ["10", "SH2"], shape: "circle", holeDiameter: "0.9mm", outerDiameter: "1.5mm", pcbX: "-1.5mm", pcbY: "-4.8mm" }),
            /* @__PURE__ */ jsx("platedhole", { portHints: ["11", "SH3"], shape: "circle", holeDiameter: "0.9mm", outerDiameter: "1.5mm", pcbX: "2.0mm", pcbY: "4.8mm" }),
            /* @__PURE__ */ jsx("platedhole", { portHints: ["12", "SH4"], shape: "circle", holeDiameter: "0.9mm", outerDiameter: "1.5mm", pcbX: "2.0mm", pcbY: "-4.8mm" })
          ] })
        }
      ),
      /* @__PURE__ */ jsx(
        "resistor",
        {
          name: "R_CC1",
          resistance: "5.1k",
          footprint: "0402",
          pcbX: "-22.5mm",
          pcbY: "11.0mm",
          schX: -8.5,
          schY: 2.5
        }
      ),
      /* @__PURE__ */ jsx(
        "resistor",
        {
          name: "R_CC2",
          resistance: "5.1k",
          footprint: "0402",
          pcbX: "-19.5mm",
          pcbY: "-6.0mm",
          schX: -8.5,
          schY: -2.5
        }
      ),
      /* @__PURE__ */ jsx(
        "chip",
        {
          name: "D_TVS",
          footprint: "sot23_6",
          pcbX: "-19.0mm",
          pcbY: "0.2mm",
          schX: -7.5,
          schY: 0,
          internallyConnectedPins: [["1", "6"], ["3", "4"]],
          pinLabels: {
            pin1: "IO1_IN",
            pin2: "GND",
            pin3: "IO2_IN",
            pin4: "IO2_OUT",
            pin5: "VBUS",
            pin6: "IO1_OUT"
          }
        }
      ),
      /* @__PURE__ */ jsx(
        "capacitor",
        {
          name: "C_IN1",
          capacitance: "10uF",
          footprint: "0805",
          maxDecouplingTraceLength: "100mm",
          pcbX: "-14.5mm",
          pcbY: "8.5mm",
          schX: -5.5,
          schY: 6
        }
      ),
      /* @__PURE__ */ jsx(
        "capacitor",
        {
          name: "C_IN2",
          capacitance: "100nF",
          footprint: "0402",
          maxDecouplingTraceLength: "100mm",
          pcbX: "-11.0mm",
          pcbY: "8.5mm",
          schX: -3.8,
          schY: 6
        }
      ),
      /* @__PURE__ */ jsx(
        "chip",
        {
          name: "U_BUCK",
          footprint: "sot23_6",
          pcbX: "-7.0mm",
          pcbY: "9.0mm",
          schX: -1.5,
          schY: 6.5,
          pinLabels: {
            pin1: "GND",
            pin2: "SW",
            pin3: "VIN",
            pin4: "FB",
            pin5: "EN",
            pin6: "BST"
          }
        }
      ),
      /* @__PURE__ */ jsx(
        "capacitor",
        {
          name: "C_BST",
          capacitance: "100nF",
          footprint: "0402",
          maxDecouplingTraceLength: "100mm",
          pcbX: "-7.0mm",
          pcbY: "5.2mm",
          schX: -1.5,
          schY: 9.5
        }
      ),
      /* @__PURE__ */ jsx(
        "diode",
        {
          name: "D_BUCK",
          footprint: "sod123",
          pcbX: "-3.5mm",
          pcbY: "6.0mm",
          schX: 0.5,
          schY: 4
        }
      ),
      /* @__PURE__ */ jsx(
        "inductor",
        {
          name: "L_BUCK",
          inductance: "4.7uH",
          footprint: "0805",
          pcbX: "-1.5mm",
          pcbY: "10.0mm",
          schX: 1.8,
          schY: 6.5
        }
      ),
      /* @__PURE__ */ jsx(
        "resistor",
        {
          name: "R_FB1",
          resistance: "31.6k",
          footprint: "0402",
          pcbX: "-3.5mm",
          pcbY: "13.0mm",
          schX: 1.8,
          schY: 9.2
        }
      ),
      /* @__PURE__ */ jsx(
        "resistor",
        {
          name: "R_FB2",
          resistance: "10k",
          footprint: "0402",
          pcbX: "-3.5mm",
          pcbY: "15.0mm",
          schX: 1.8,
          schY: 11
        }
      ),
      /* @__PURE__ */ jsx(
        "capacitor",
        {
          name: "C_OUT1",
          capacitance: "22uF",
          footprint: "0805",
          maxDecouplingTraceLength: "100mm",
          pcbX: "3.0mm",
          pcbY: "10.5mm",
          schX: 4.5,
          schY: 6.5
        }
      ),
      /* @__PURE__ */ jsx(
        "capacitor",
        {
          name: "C_OUT2",
          capacitance: "22uF",
          footprint: "0805",
          maxDecouplingTraceLength: "100mm",
          pcbX: "6.2mm",
          pcbY: "10.5mm",
          schX: 6,
          schY: 6.5
        }
      ),
      /* @__PURE__ */ jsx(
        "capacitor",
        {
          name: "C_LDO_IN",
          capacitance: "1uF",
          footprint: "0402",
          maxDecouplingTraceLength: "100mm",
          pcbX: "9.2mm",
          pcbY: "10.5mm",
          schX: 8,
          schY: 6.5
        }
      ),
      /* @__PURE__ */ jsx(
        "chip",
        {
          name: "U_LDO",
          footprint: "sot23_5",
          pcbX: "12.2mm",
          pcbY: "10.5mm",
          schX: 10.2,
          schY: 6.5,
          noConnect: ["pin4"],
          pinLabels: {
            pin1: "IN",
            pin2: "GND",
            pin3: "EN",
            pin4: "NC",
            pin5: "OUT"
          }
        }
      ),
      /* @__PURE__ */ jsx(
        "capacitor",
        {
          name: "C_ANA1",
          capacitance: "100nF",
          footprint: "0402",
          maxDecouplingTraceLength: "100mm",
          pcbX: "15.2mm",
          pcbY: "11.2mm",
          schX: 12.5,
          schY: 6.5
        }
      ),
      /* @__PURE__ */ jsx(
        "capacitor",
        {
          name: "C_ANA2",
          capacitance: "10nF",
          footprint: "0402",
          maxDecouplingTraceLength: "100mm",
          pcbX: "9.5mm",
          pcbY: "-2.2mm",
          schX: 14,
          schY: 6.5
        }
      ),
      /* @__PURE__ */ jsx(
        "chip",
        {
          name: "U_MCU",
          pcbX: "3.0mm",
          pcbY: "0mm",
          schX: 0,
          schY: -1,
          noConnect: ["pin18", "pin19"],
          pinLabels: {
            pin1: "VDD3V3_A",
            pin2: "GND_IO",
            pin3: "USB_DP",
            pin4: "USB_DM",
            pin5: "GND_OSC",
            pin6: "VREF_2V5",
            pin7: "VDDA_2V5",
            pin8: "ADC_IN1",
            pin9: "ADC_IN2",
            pin10: "LED_STAT",
            pin11: "PWM_ACT1",
            pin12: "PWM_ACT2",
            pin13: "UART_TX",
            pin14: "UART_RX",
            pin15: "GND_DIG",
            pin16: "VDD3V3_B",
            pin17: "RF_TX",
            pin18: "SWDIO",
            pin19: "SWCLK",
            pin20: "VDD_CORE",
            pin21: "BOOT0",
            pin22: "GND_PLL",
            pin23: "NRST",
            pin24: "VDD3V3_C",
            pin25: "DAP_GND"
          },
          footprint: /* @__PURE__ */ jsxs("footprint", { children: [
            /* @__PURE__ */ jsx("smtpad", { portHints: ["1"], shape: "rect", width: "0.8mm", height: "0.26mm", pcbX: "-1.9mm", pcbY: "1.25mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["2"], shape: "rect", width: "0.8mm", height: "0.26mm", pcbX: "-1.9mm", pcbY: "0.75mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["3"], shape: "rect", width: "0.8mm", height: "0.26mm", pcbX: "-1.9mm", pcbY: "0.25mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["4"], shape: "rect", width: "0.8mm", height: "0.26mm", pcbX: "-1.9mm", pcbY: "-0.25mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["5"], shape: "rect", width: "0.8mm", height: "0.26mm", pcbX: "-1.9mm", pcbY: "-0.75mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["6"], shape: "rect", width: "0.8mm", height: "0.26mm", pcbX: "-1.9mm", pcbY: "-1.25mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["7"], shape: "rect", width: "0.26mm", height: "0.8mm", pcbX: "-1.25mm", pcbY: "-1.9mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["8"], shape: "rect", width: "0.26mm", height: "0.8mm", pcbX: "-0.75mm", pcbY: "-1.9mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["9"], shape: "rect", width: "0.26mm", height: "0.8mm", pcbX: "-0.25mm", pcbY: "-1.9mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["10"], shape: "rect", width: "0.26mm", height: "0.8mm", pcbX: "0.25mm", pcbY: "-1.9mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["11"], shape: "rect", width: "0.26mm", height: "0.8mm", pcbX: "0.75mm", pcbY: "-1.9mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["12"], shape: "rect", width: "0.26mm", height: "0.8mm", pcbX: "1.25mm", pcbY: "-1.9mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["13"], shape: "rect", width: "0.8mm", height: "0.26mm", pcbX: "1.9mm", pcbY: "-1.25mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["14"], shape: "rect", width: "0.8mm", height: "0.26mm", pcbX: "1.9mm", pcbY: "-0.75mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["15"], shape: "rect", width: "0.8mm", height: "0.26mm", pcbX: "1.9mm", pcbY: "-0.25mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["16"], shape: "rect", width: "0.8mm", height: "0.26mm", pcbX: "1.9mm", pcbY: "0.25mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["17"], shape: "rect", width: "0.8mm", height: "0.26mm", pcbX: "1.9mm", pcbY: "0.75mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["18"], shape: "rect", width: "0.8mm", height: "0.26mm", pcbX: "1.9mm", pcbY: "1.25mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["19"], shape: "rect", width: "0.26mm", height: "0.8mm", pcbX: "1.25mm", pcbY: "1.9mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["20"], shape: "rect", width: "0.26mm", height: "0.8mm", pcbX: "0.75mm", pcbY: "1.9mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["21"], shape: "rect", width: "0.26mm", height: "0.8mm", pcbX: "0.25mm", pcbY: "1.9mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["22"], shape: "rect", width: "0.26mm", height: "0.8mm", pcbX: "-0.25mm", pcbY: "1.9mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["23"], shape: "rect", width: "0.26mm", height: "0.8mm", pcbX: "-0.75mm", pcbY: "1.9mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["24"], shape: "rect", width: "0.26mm", height: "0.8mm", pcbX: "-1.25mm", pcbY: "1.9mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["25", "DAP_GND"], shape: "rect", width: "2.4mm", height: "2.4mm", pcbX: "0mm", pcbY: "0mm" }),
            /* @__PURE__ */ jsx("via", { pcbX: "-0.55mm", pcbY: "0.55mm", holeDiameter: "0.3mm", outerDiameter: "0.55mm", connectsTo: "net.GND" }),
            /* @__PURE__ */ jsx("via", { pcbX: "0.55mm", pcbY: "0.55mm", holeDiameter: "0.3mm", outerDiameter: "0.55mm", connectsTo: "net.GND" }),
            /* @__PURE__ */ jsx("via", { pcbX: "-0.55mm", pcbY: "-0.55mm", holeDiameter: "0.3mm", outerDiameter: "0.55mm", connectsTo: "net.GND" }),
            /* @__PURE__ */ jsx("via", { pcbX: "0.55mm", pcbY: "-0.55mm", holeDiameter: "0.3mm", outerDiameter: "0.55mm", connectsTo: "net.GND" })
          ] })
        }
      ),
      /* @__PURE__ */ jsx("capacitor", { name: "C_MCU1", capacitance: "100nF", footprint: "0402", maxDecouplingTraceLength: "100mm", pcbX: "-0.5mm", pcbY: "4.5mm", schX: -2.5, schY: 2.5 }),
      /* @__PURE__ */ jsx("capacitor", { name: "C_MCU2", capacitance: "100nF", footprint: "0402", maxDecouplingTraceLength: "100mm", pcbX: "6.5mm", pcbY: "4.2mm", schX: 2.5, schY: 2.5 }),
      /* @__PURE__ */ jsx("capacitor", { name: "C_MCU_ANA", capacitance: "100nF", footprint: "0402", maxDecouplingTraceLength: "100mm", pcbX: "-0.2mm", pcbY: "-5.0mm", schX: -2.5, schY: -4.5 }),
      /* @__PURE__ */ jsx("resistor", { name: "R_NRST", resistance: "10k", footprint: "0402", pcbX: "1.8mm", pcbY: "4.5mm", schX: -4.5, schY: -2 }),
      /* @__PURE__ */ jsx("resistor", { name: "R_ADC1", resistance: "1k", footprint: "0402", pcbX: "7.5mm", pcbY: "-7.0mm", schX: 4.5, schY: -5 }),
      /* @__PURE__ */ jsx("resistor", { name: "R_ADC2", resistance: "1k", footprint: "0402", pcbX: "7.5mm", pcbY: "-10.0mm", schX: 4.5, schY: -7 }),
      /* @__PURE__ */ jsx("resistor", { name: "R_LED", resistance: "1k", footprint: "0402", pcbX: "12.5mm", pcbY: "-4.5mm", schX: 4.5, schY: -2 }),
      /* @__PURE__ */ jsx("led", { name: "D_LED", color: "green", footprint: "0603", pcbX: "16.0mm", pcbY: "-4.5mm", schX: 7, schY: -2 }),
      /* @__PURE__ */ jsx(
        "chip",
        {
          name: "J_RF",
          pcbX: "19.5mm",
          pcbY: "3.0mm",
          schX: 8,
          schY: 2,
          pinLabels: { pin1: "SIG", pin2: "GND1", pin3: "GND2" },
          footprint: /* @__PURE__ */ jsxs("footprint", { children: [
            /* @__PURE__ */ jsx("smtpad", { portHints: ["1", "SIG"], shape: "rect", width: "1.0mm", height: "0.8mm", pcbX: "-1.2mm", pcbY: "0mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["2", "GND1"], shape: "rect", width: "1.0mm", height: "1.2mm", pcbX: "0.8mm", pcbY: "1.4mm" }),
            /* @__PURE__ */ jsx("smtpad", { portHints: ["3", "GND2"], shape: "rect", width: "1.0mm", height: "1.2mm", pcbX: "0.8mm", pcbY: "-1.4mm" })
          ] })
        }
      ),
      /* @__PURE__ */ jsx(
        "pinheader",
        {
          name: "J_ACT",
          pinCount: 6,
          pitch: "2.54mm",
          pcbX: "22.5mm",
          pcbY: "-5.5mm",
          pcbRotation: 90,
          schX: 11.5,
          schY: -4.5,
          pinLabels: ["3V3", "GND", "PWM1", "PWM2", "SENSE1", "SENSE2"]
        }
      ),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_j1", from: ".J_USB > .GND1", to: "net.GND", thickness: "0.4mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_j2", from: ".J_USB > .GND2", to: "net.GND", thickness: "0.4mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_sh1", from: ".J_USB > .SH1", to: "net.GND", thickness: "0.4mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_sh2", from: ".J_USB > .SH2", to: "net.GND", thickness: "0.4mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_sh3", from: ".J_USB > .SH3", to: "net.GND", thickness: "0.4mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_sh4", from: ".J_USB > .SH4", to: "net.GND", thickness: "0.4mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_tvs", from: ".D_TVS > .GND", to: "net.GND", thickness: "0.25mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_cin1", from: ".C_IN1 > .pin2", to: "net.GND", thickness: "0.4mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_cin2", from: ".C_IN2 > .pin2", to: "net.GND", thickness: "0.3mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_buck", from: ".U_BUCK > .GND", to: "net.GND", thickness: "0.4mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_dbuck", from: ".D_BUCK > .anode", to: "net.GND", thickness: "0.4mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_rfb2", from: ".R_FB2 > .pin2", to: "net.GND", thickness: "0.25mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_cout1", from: ".C_OUT1 > .pin2", to: "net.GND", thickness: "0.4mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_cout2", from: ".C_OUT2 > .pin2", to: "net.GND", thickness: "0.4mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_cldo", from: ".C_LDO_IN > .pin2", to: "net.GND", thickness: "0.3mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_ldo", from: ".U_LDO > .GND", to: "net.GND", thickness: "0.3mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_cana1", from: ".C_ANA1 > .pin2", to: "net.GND", thickness: "0.3mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_cana2", from: ".C_ANA2 > .pin2", to: "net.GND", thickness: "0.3mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_mcu_dap", from: ".U_MCU > .DAP_GND", to: "net.GND", thickness: "0.4mm" }),
      /* @__PURE__ */ jsx(
        "trace",
        {
          name: "tr_gnd_mcu_io",
          from: ".U_MCU > .GND_IO",
          to: ".U_MCU > .DAP_GND",
          thickness: "0.25mm"
        }
      ),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_mcu_dig", from: ".U_MCU > .GND_DIG", to: "net.GND", thickness: "0.25mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_mcu_boot", from: ".U_MCU > .BOOT0", to: "net.GND", thickness: "0.25mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_mcu_pll", from: ".U_MCU > .GND_PLL", to: "net.GND", thickness: "0.25mm" }),
      /* @__PURE__ */ jsx(
        "trace",
        {
          name: "tr_gnd_mcu_osc",
          from: ".U_MCU > .GND_OSC",
          to: ".U_MCU > .DAP_GND",
          thickness: "0.25mm"
        }
      ),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_cmcu1", from: ".C_MCU1 > .pin2", to: "net.GND", thickness: "0.25mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_cmcu2", from: ".C_MCU2 > .pin2", to: "net.GND", thickness: "0.25mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_cmcu_ana", from: ".C_MCU_ANA > .pin2", to: "net.GND", thickness: "0.25mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_led", from: ".D_LED > .cathode", to: "net.GND", thickness: "0.25mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_rf1", from: ".J_RF > .GND1", to: "net.GND", thickness: "0.3mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_rf2", from: ".J_RF > .GND2", to: "net.GND", thickness: "0.3mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_gnd_jact", from: ".J_ACT > .pin2", to: "net.GND", thickness: "0.4mm" }),
      /* @__PURE__ */ jsx(
        "trace",
        {
          name: "tr_cc1",
          from: ".J_USB > .CC1",
          to: ".R_CC1 > .pin1",
          thickness: "0.25mm",
          pcbRouteHints: [
            { x: -24.2, y: 1.5, via: true, to_layer: "bottom" },
            { x: -23.5, y: 9.5, via: true, to_layer: "top" }
          ]
        }
      ),
      /* @__PURE__ */ jsx("trace", { name: "tr_cc1_gnd", from: ".R_CC1 > .pin2", to: "net.GND", thickness: "0.25mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_cc2", from: ".J_USB > .CC2", to: ".R_CC2 > .pin1", thickness: "0.25mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_cc2_gnd", from: ".R_CC2 > .pin2", to: "net.GND", thickness: "0.25mm" }),
      /* @__PURE__ */ jsx(
        "trace",
        {
          name: "tr_usb_dp_in",
          from: ".J_USB > .DP",
          to: ".D_TVS > .IO1_IN",
          thickness: "0.211mm",
          maxViaCount: 0
        }
      ),
      /* @__PURE__ */ jsx(
        "trace",
        {
          name: "tr_usb_dp_out",
          from: ".D_TVS > .IO1_OUT",
          to: ".U_MCU > .USB_DP",
          thickness: "0.211mm",
          maxViaCount: 0
        }
      ),
      /* @__PURE__ */ jsx(
        "trace",
        {
          name: "tr_usb_dm_in",
          from: ".J_USB > .DM",
          to: ".D_TVS > .IO2_IN",
          thickness: "0.211mm",
          maxViaCount: 0
        }
      ),
      /* @__PURE__ */ jsx(
        "trace",
        {
          name: "tr_usb_dm_out",
          from: ".D_TVS > .IO2_OUT",
          to: ".U_MCU > .USB_DM",
          thickness: "0.211mm",
          maxViaCount: 0
        }
      ),
      /* @__PURE__ */ jsx(
        "trace",
        {
          name: "tr_vbus_trunk1",
          from: ".J_USB > .VBUS1",
          to: ".C_IN1 > .pin1",
          thickness: "2.1mm",
          pcbRouteHints: [{ x: -18.5, y: 8.5 }]
        }
      ),
      /* @__PURE__ */ jsx("trace", { name: "tr_vbus_trunk2", from: ".C_IN1 > .pin1", to: ".C_IN2 > .pin1", thickness: "2.1mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_vbus_buck", from: ".C_IN2 > .pin1", to: ".U_BUCK > .VIN", thickness: "2.1mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_vbus_en", from: ".U_BUCK > .EN", to: ".C_IN2 > .pin1", thickness: "0.25mm" }),
      /* @__PURE__ */ jsx(
        "trace",
        {
          name: "tr_vbus_j2",
          from: ".J_USB > .VBUS2",
          to: ".C_IN1 > .pin1",
          thickness: "0.8mm",
          pcbRouteHints: [
            { x: -21, y: -3.5, via: true, to_layer: "bottom" },
            { x: -16, y: 8.5, via: true, to_layer: "top" }
          ]
        }
      ),
      /* @__PURE__ */ jsx(
        "trace",
        {
          name: "tr_vbus_tvs",
          from: ".D_TVS > .VBUS",
          to: ".C_IN1 > .pin1",
          thickness: "0.3mm",
          pcbRouteHints: [
            { x: -18, y: 2.6, via: true, to_layer: "bottom" },
            { x: -14.5, y: 5.5, via: true, to_layer: "top" }
          ]
        }
      ),
      /* @__PURE__ */ jsx("trace", { name: "tr_buck_sw_l", from: ".U_BUCK > .SW", to: ".L_BUCK > .pin1", thickness: "0.6mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_buck_sw_d", from: ".U_BUCK > .SW", to: ".D_BUCK > .cathode", thickness: "0.5mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_buck_bst1", from: ".U_BUCK > .BST", to: ".C_BST > .pin1", thickness: "0.25mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_buck_bst2", from: ".C_BST > .pin2", to: ".U_BUCK > .SW", thickness: "0.25mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_buck_fb1", from: ".U_BUCK > .FB", to: ".R_FB1 > .pin2", thickness: "0.25mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_buck_fb2", from: ".R_FB1 > .pin2", to: ".R_FB2 > .pin1", thickness: "0.25mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_fb_3v3", from: ".R_FB1 > .pin1", to: ".C_OUT1 > .pin1", thickness: "0.25mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_3v3_out1", from: ".L_BUCK > .pin2", to: ".C_OUT1 > .pin1", thickness: "0.8mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_3v3_out2", from: ".C_OUT1 > .pin1", to: ".C_OUT2 > .pin1", thickness: "0.8mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_3v3_ldo_in", from: ".C_OUT2 > .pin1", to: ".C_LDO_IN > .pin1", thickness: "0.6mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_3v3_ldo", from: ".C_LDO_IN > .pin1", to: ".U_LDO > .IN", thickness: "0.5mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_3v3_ldo_en", from: ".U_LDO > .EN", to: ".C_LDO_IN > .pin1", thickness: "0.25mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_3v3_mcu_a", from: ".C_OUT1 > .pin1", to: ".U_MCU > .VDD3V3_A", thickness: "0.4mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_3v3_mcu_b", from: ".C_OUT2 > .pin1", to: ".U_MCU > .VDD3V3_B", thickness: "0.4mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_3v3_mcu_c", from: ".C_OUT1 > .pin1", to: ".U_MCU > .VDD3V3_C", thickness: "0.4mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_3v3_mcu_core", from: ".C_OUT2 > .pin1", to: ".U_MCU > .VDD_CORE", thickness: "0.4mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_3v3_cmcu1", from: ".C_MCU1 > .pin1", to: ".U_MCU > .VDD3V3_C", thickness: "0.3mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_3v3_cmcu2", from: ".C_MCU2 > .pin1", to: ".U_MCU > .VDD3V3_B", thickness: "0.3mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_3v3_nrst_pu", from: ".R_NRST > .pin1", to: ".C_MCU2 > .pin1", thickness: "0.25mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_3v3_jact", from: ".C_OUT2 > .pin1", to: ".J_ACT > .pin1", thickness: "0.5mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_2v5_c1", from: ".U_LDO > .OUT", to: ".C_ANA1 > .pin1", thickness: "0.35mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_2v5_c2", from: ".C_ANA1 > .pin1", to: ".C_ANA2 > .pin1", thickness: "0.35mm" }),
      /* @__PURE__ */ jsx(
        "trace",
        {
          name: "tr_2v5_vdda",
          from: ".C_ANA2 > .pin1",
          to: ".C_MCU_ANA > .pin1",
          thickness: "0.3mm"
        }
      ),
      /* @__PURE__ */ jsx("trace", { name: "tr_2v5_vref", from: ".U_MCU > .VDDA_2V5", to: ".U_MCU > .VREF_2V5", thickness: "0.3mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_2v5_cmcu", from: ".C_MCU_ANA > .pin1", to: ".U_MCU > .VDDA_2V5", thickness: "0.3mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_nrst", from: ".R_NRST > .pin2", to: ".U_MCU > .NRST", thickness: "0.2mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_adc1_in", from: ".U_MCU > .ADC_IN1", to: ".R_ADC1 > .pin1", thickness: "0.2mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_adc1_ext", from: ".R_ADC1 > .pin2", to: ".J_ACT > .pin5", thickness: "0.2mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_adc2_in", from: ".U_MCU > .ADC_IN2", to: ".R_ADC2 > .pin1", thickness: "0.2mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_adc2_ext", from: ".R_ADC2 > .pin2", to: ".J_ACT > .pin6", thickness: "0.2mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_pwm1", from: ".U_MCU > .PWM_ACT1", to: ".J_ACT > .pin3", thickness: "0.25mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_pwm2", from: ".U_MCU > .PWM_ACT2", to: ".J_ACT > .pin4", thickness: "0.25mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_led_r", from: ".U_MCU > .LED_STAT", to: ".R_LED > .pin1", thickness: "0.2mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_led_d", from: ".R_LED > .pin2", to: ".D_LED > .anode", thickness: "0.2mm" }),
      /* @__PURE__ */ jsx("trace", { name: "tr_rf_50", from: ".U_MCU > .RF_TX", to: ".J_RF > .SIG", thickness: "0.306mm" })
    ]
  }
);
var board1_default = Board1;
export {
  Board1,
  board1_default as default
};
