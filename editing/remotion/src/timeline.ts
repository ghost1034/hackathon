// The edit spec that the AI writes and the renderer executes. Mirrors TRD §6.1, plus
// `source` on each clip (so one timeline can pull from several ingested videos) and
// `src`, which render.py fills in with the staged clip's path inside public/.

export type StyleName = "cinematic" | "reel" | "vlog";
export type TransitionKind = "cut" | "fade" | "slide" | "wipe";
export type Motion = "push_in" | "pull_out" | "punch" | "none";
export type OverlayStyle = "hook" | "caption" | "punch";

export type Clip = {
  id: string;
  source: string;
  in_s: number;
  out_s: number;
  transition_in?: TransitionKind;
  transition_s?: number;
  caption?: string | null;
  motion?: Motion;
  volume?: number;
  // Where to frame the shot when the output aspect differs from the source (0 = left/top, 1 = right/bottom).
  // Applied by render.py when cutting.
  crop_x?: number;
  crop_y?: number;
  src?: string;
};

// Text on the whole-video timeline, independent of clip boundaries.
export type Overlay = {
  text: string;
  start_s: number;
  end_s: number;
  position?: "top" | "center" | "bottom";
  style?: OverlayStyle;
};

export type Effect =
  | {type: "flash"; at_s: number; duration_s?: number}
  | {type: "fade_out"; duration_s: number};

export type Timeline = {
  version: 1;
  style: StyleName;
  output: {width: number; height: number; fps: number};
  title?: {text: string; subtitle?: string | null; duration_s: number} | null;
  clips: Clip[];
  music?: {track: string; gain_db: number; duck_original_audio: boolean; duck_volume?: number; src?: string} | null;
  overlays?: Overlay[];
  effects?: Effect[];
  end_card?: {text: string; duration_s: number} | null;
};

export const toFrames = (seconds: number, fps: number) => Math.max(1, Math.round(seconds * fps));

const END_CARD_TRANSITION_S = 0.75;

// Frame layout shared by the composition and calculateMetadata so they can never disagree.
export const layout = (t: Timeline) => {
  const fps = t.output.fps;
  const clips = t.clips.map((clip, i) => {
    const frames = toFrames(clip.out_s - clip.in_s, fps);
    const kind = i === 0 ? "cut" : (clip.transition_in ?? "cut");
    const prev = i === 0 ? 0 : toFrames(t.clips[i - 1].out_s - t.clips[i - 1].in_s, fps);
    const transitionFrames =
      kind === "cut" ? 0 : Math.min(toFrames(clip.transition_s ?? 0.5, fps), frames - 1, prev - 1);
    return {clip, frames, kind, transitionFrames};
  });
  const endFrames = t.end_card ? toFrames(t.end_card.duration_s, fps) : 0;
  const lastFrames = clips.at(-1)?.frames ?? 0;
  const endTransitionFrames = t.end_card
    ? Math.min(toFrames(END_CARD_TRANSITION_S, fps), endFrames - 1, lastFrames - 1)
    : 0;
  const total =
    clips.reduce((sum, c) => sum + c.frames - c.transitionFrames, 0) + endFrames - endTransitionFrames;
  return {clips, endFrames, endTransitionFrames, total: Math.max(1, total)};
};
