import {loadFont as loadAnton} from "@remotion/google-fonts/Anton";
import {loadFont as loadCinzel} from "@remotion/google-fonts/Cinzel";
import {loadFont as loadInter} from "@remotion/google-fonts/Inter";
import {loadFont as loadMontserrat} from "@remotion/google-fonts/Montserrat";
import type {Motion, StyleName} from "./timeline";

const cinzel = loadCinzel("normal", {weights: ["400", "700"], subsets: ["latin"]});
const inter = loadInter("normal", {weights: ["400", "600"], subsets: ["latin"]});
const anton = loadAnton("normal", {weights: ["400"], subsets: ["latin"]});
const montserrat = loadMontserrat("normal", {weights: ["500", "800"], subsets: ["latin"]});

export type StylePreset = {
  titleFont: string;
  bodyFont: string;
  // Timeline overlays: "hook"/"caption" use overlayFont, "punch" uses punchFont.
  overlayFont: string;
  punchFont: string;
  titleTransform: "uppercase" | "none";
  // Height of each letterbox bar as a fraction of frame height; 0 disables.
  letterbox: number;
  // CSS filter applied to every clip as a light color grade.
  grade: string;
  motion: Motion;
};

export const STYLES: Record<StyleName, StylePreset> = {
  cinematic: {
    titleFont: cinzel.fontFamily,
    bodyFont: inter.fontFamily,
    overlayFont: inter.fontFamily,
    punchFont: cinzel.fontFamily,
    titleTransform: "uppercase",
    letterbox: 0.128, // 16:9 -> 2.39:1
    grade: "contrast(1.08) saturate(1.12) brightness(0.98)",
    motion: "push_in",
  },
  reel: {
    titleFont: anton.fontFamily,
    bodyFont: inter.fontFamily,
    overlayFont: montserrat.fontFamily,
    punchFont: anton.fontFamily,
    titleTransform: "uppercase",
    letterbox: 0,
    grade: "contrast(1.05) saturate(1.2)",
    motion: "none",
  },
  vlog: {
    titleFont: montserrat.fontFamily,
    bodyFont: montserrat.fontFamily,
    overlayFont: montserrat.fontFamily,
    punchFont: montserrat.fontFamily,
    titleTransform: "none",
    letterbox: 0,
    grade: "saturate(1.05)",
    motion: "none",
  },
};
