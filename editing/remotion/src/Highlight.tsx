import {TransitionSeries, linearTiming, type TransitionPresentation} from "@remotion/transitions";
import {fade} from "@remotion/transitions/fade";
import {slide} from "@remotion/transitions/slide";
import {wipe} from "@remotion/transitions/wipe";
import React from "react";
import {
  AbsoluteFill,
  Audio,
  Easing,
  OffthreadVideo,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import {STYLES, type StylePreset} from "./styles";
import {layout, toFrames, type Clip, type Overlay, type Timeline, type TransitionKind} from "./timeline";

export type HighlightProps = {timeline: Timeline | null};

const DUCKED_CLIP_VOLUME = 0.2;

const presentationFor = (kind: TransitionKind): TransitionPresentation<Record<string, unknown>> => {
  switch (kind) {
    case "slide":
      return slide({direction: "from-right"}) as TransitionPresentation<Record<string, unknown>>;
    case "wipe":
      return wipe({direction: "from-left"}) as TransitionPresentation<Record<string, unknown>>;
    default:
      return fade() as TransitionPresentation<Record<string, unknown>>;
  }
};

const ClipView: React.FC<{clip: Clip; frames: number; preset: StylePreset; volume: number}> = ({
  clip,
  frames,
  preset,
  volume,
}) => {
  const frame = useCurrentFrame();
  const motion = clip.motion ?? preset.motion;
  const progress = frame / Math.max(1, frames - 1);
  const punch = interpolate(frame, [0, 7], [1.14, 1], {extrapolateRight: "clamp", easing: Easing.out(Easing.cubic)});
  const scale =
    motion === "push_in" ? 1 + 0.06 * progress
    : motion === "pull_out" ? 1.06 - 0.06 * progress
    : motion === "punch" ? punch + 0.03 * progress
    : 1;

  if (!clip.src) {
    return <AbsoluteFill style={{backgroundColor: "#300", color: "white"}}>missing src for {clip.id}</AbsoluteFill>;
  }
  return (
    <AbsoluteFill style={{backgroundColor: "black", overflow: "hidden"}}>
      <OffthreadVideo
        src={staticFile(clip.src)}
        volume={volume * (clip.volume ?? 1)}
        style={{width: "100%", height: "100%", objectFit: "cover", transform: `scale(${scale})`, filter: preset.grade}}
      />
      {clip.caption ? <Caption text={clip.caption} frames={frames} preset={preset} /> : null}
    </AbsoluteFill>
  );
};

const Caption: React.FC<{text: string; frames: number; preset: StylePreset}> = ({text, frames, preset}) => {
  const frame = useCurrentFrame();
  const {fps, height} = useVideoConfig();
  const fadeFrames = Math.round(fps * 0.4);
  const opacity = interpolate(frame, [fadeFrames, fadeFrames * 2, frames - fadeFrames * 2, frames - fadeFrames], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const rise = interpolate(frame, [fadeFrames, fadeFrames * 2], [12, 0], {extrapolateLeft: "clamp", extrapolateRight: "clamp"});
  return (
    <AbsoluteFill style={{justifyContent: "flex-end", alignItems: "center"}}>
      <div
        style={{
          marginBottom: height * (preset.letterbox + 0.06),
          opacity,
          transform: `translateY(${rise}px)`,
          fontFamily: preset.bodyFont,
          fontWeight: 600,
          fontSize: height * 0.038,
          color: "white",
          letterSpacing: "0.02em",
          textShadow: "0 2px 12px rgba(0,0,0,0.7)",
        }}
      >
        {text}
      </div>
    </AbsoluteFill>
  );
};

const TitleOverlay: React.FC<{title: NonNullable<Timeline["title"]>; frames: number; preset: StylePreset}> = ({
  title,
  frames,
  preset,
}) => {
  const frame = useCurrentFrame();
  const {fps, height} = useVideoConfig();
  const inEnd = Math.round(fps * 1.0);
  const outStart = frames - Math.round(fps * 0.6);
  const opacity = interpolate(frame, [0, inEnd, outStart, frames], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const tracking = interpolate(frame, [0, frames], [0.2, 0.12], {easing: Easing.out(Easing.quad)});
  const subOpacity = interpolate(frame, [inEnd * 0.6, inEnd * 1.4], [0, 1], {extrapolateLeft: "clamp", extrapolateRight: "clamp"});
  return (
    <AbsoluteFill style={{justifyContent: "center", alignItems: "center", opacity}}>
      <AbsoluteFill style={{background: "radial-gradient(ellipse at center, rgba(0,0,0,0.45) 0%, rgba(0,0,0,0) 65%)"}} />
      <div
        style={{
          fontFamily: preset.titleFont,
          fontWeight: 700,
          fontSize: height * 0.085,
          maxWidth: "88%",
          color: "white",
          textTransform: preset.titleTransform,
          letterSpacing: `${tracking}em`,
          textAlign: "center",
          textShadow: "0 4px 24px rgba(0,0,0,0.6)",
        }}
      >
        {title.text}
      </div>
      {title.subtitle ? (
        <div
          style={{
            opacity: subOpacity,
            marginTop: height * 0.02,
            fontFamily: preset.bodyFont,
            fontSize: height * 0.032,
            color: "rgba(255,255,255,0.85)",
            letterSpacing: "0.3em",
            textTransform: "uppercase",
            textShadow: "0 2px 10px rgba(0,0,0,0.8)",
          }}
        >
          {title.subtitle}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};

const EndCard: React.FC<{text: string; preset: StylePreset}> = ({text, preset}) => {
  const frame = useCurrentFrame();
  const {fps, height} = useVideoConfig();
  const opacity = interpolate(frame, [fps * 0.4, fps * 1.2], [0, 1], {extrapolateLeft: "clamp", extrapolateRight: "clamp"});
  return (
    <AbsoluteFill style={{backgroundColor: "black", justifyContent: "center", alignItems: "center"}}>
      <div style={{opacity, fontFamily: preset.bodyFont, fontSize: height * 0.03, color: "rgba(255,255,255,0.8)", letterSpacing: "0.15em"}}>
        {text}
      </div>
    </AbsoluteFill>
  );
};

const Letterbox: React.FC<{fraction: number}> = ({fraction}) => {
  if (fraction <= 0) return null;
  const bar: React.CSSProperties = {position: "absolute", left: 0, right: 0, height: `${fraction * 100}%`, backgroundColor: "black"};
  return (
    <AbsoluteFill>
      <div style={{...bar, top: 0}} />
      <div style={{...bar, bottom: 0}} />
    </AbsoluteFill>
  );
};

const FadeFromBlack: React.FC<{frames: number}> = ({frames}) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, frames], [1, 0], {extrapolateRight: "clamp"});
  return <AbsoluteFill style={{backgroundColor: "black", opacity}} />;
};

// Social-style text: "hook" is dark text on a white card, "caption" is bold outlined text,
// "punch" is oversized display type. All pop in with a spring and fade out at the end.
const OverlayText: React.FC<{overlay: Overlay; frames: number; preset: StylePreset}> = ({overlay, frames, preset}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const style = overlay.style ?? "caption";
  const pop = spring({frame, fps, config: {damping: style === "punch" ? 9 : 14, stiffness: 220}});
  const scale = interpolate(pop, [0, 1], [style === "punch" ? 1.6 : 0.7, 1]);
  const opacity = interpolate(frame, [0, 3, frames - 4, frames], [0, 1, 1, 0], {extrapolateLeft: "clamp", extrapolateRight: "clamp"});
  const top = {top: 0.2, center: 0.42, bottom: 0.66}[overlay.position ?? "center"];
  const short = Math.min(width, height);

  const text: React.CSSProperties =
    style === "hook"
      ? {
          fontFamily: preset.overlayFont,
          fontWeight: 800,
          fontSize: short * 0.062,
          lineHeight: 1.25,
          color: "#111",
          backgroundColor: "white",
          padding: `${short * 0.02}px ${short * 0.035}px`,
          borderRadius: short * 0.025,
          boxDecorationBreak: "clone",
          WebkitBoxDecorationBreak: "clone",
        }
      : style === "punch"
        ? {
            fontFamily: preset.punchFont,
            fontSize: short * 0.16,
            lineHeight: 1.0,
            color: "white",
            textTransform: "uppercase",
            letterSpacing: "0.01em",
            WebkitTextStroke: `${short * 0.006}px black`,
            paintOrder: "stroke fill",
            textShadow: "0 8px 30px rgba(0,0,0,0.55)",
          }
        : {
            fontFamily: preset.overlayFont,
            fontWeight: 800,
            fontSize: short * 0.07,
            lineHeight: 1.15,
            color: "white",
            WebkitTextStroke: `${short * 0.008}px black`,
            paintOrder: "stroke fill",
            textShadow: "0 4px 18px rgba(0,0,0,0.6)",
          };

  return (
    <AbsoluteFill style={{alignItems: "center", opacity}}>
      <div
        style={{
          position: "absolute",
          top: `${top * 100}%`,
          width: "86%",
          textAlign: "center",
          transform: `scale(${scale})`,
        }}
      >
        <span style={text}>{overlay.text}</span>
      </div>
    </AbsoluteFill>
  );
};

const Flash: React.FC<{frames: number}> = ({frames}) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, frames], [0.85, 0], {extrapolateRight: "clamp"});
  return <AbsoluteFill style={{backgroundColor: "white", opacity}} />;
};

const FadeToBlack: React.FC<{frames: number}> = ({frames}) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, frames], [0, 1], {extrapolateRight: "clamp"});
  return <AbsoluteFill style={{backgroundColor: "black", opacity}} />;
};

export const Highlight: React.FC<HighlightProps> = ({timeline}) => {
  const {fps, durationInFrames} = useVideoConfig();
  if (!timeline) {
    return (
      <AbsoluteFill style={{backgroundColor: "black", color: "white", justifyContent: "center", alignItems: "center", fontSize: 40}}>
        No timeline staged. Run editing/render.py --stage-only.
      </AbsoluteFill>
    );
  }

  const preset = STYLES[timeline.style];
  const plan = layout(timeline);
  const music = timeline.music;
  const clipVolume = music?.duck_original_audio ? (music.duck_volume ?? DUCKED_CLIP_VOLUME) : 1;
  const musicGain = music ? Math.pow(10, music.gain_db / 20) : 0;
  const first = timeline.clips[0];

  return (
    <AbsoluteFill style={{backgroundColor: "black"}}>
      <TransitionSeries>
        {plan.clips.map(({clip, frames, kind, transitionFrames}) => (
          <React.Fragment key={clip.id}>
            {transitionFrames > 0 ? (
              <TransitionSeries.Transition
                presentation={presentationFor(kind)}
                timing={linearTiming({durationInFrames: transitionFrames})}
              />
            ) : null}
            <TransitionSeries.Sequence durationInFrames={frames}>
              <ClipView clip={clip} frames={frames} preset={preset} volume={clipVolume} />
            </TransitionSeries.Sequence>
          </React.Fragment>
        ))}
        {timeline.end_card ? (
          <>
            <TransitionSeries.Transition
              presentation={presentationFor("fade")}
              timing={linearTiming({durationInFrames: plan.endTransitionFrames})}
            />
            <TransitionSeries.Sequence durationInFrames={plan.endFrames}>
              <EndCard text={timeline.end_card.text} preset={preset} />
            </TransitionSeries.Sequence>
          </>
        ) : null}
      </TransitionSeries>

      <Letterbox fraction={preset.letterbox} />

      {first?.transition_in === "fade" ? (
        <Sequence durationInFrames={toFrames(first.transition_s ?? 0.5, fps)}>
          <FadeFromBlack frames={toFrames(first.transition_s ?? 0.5, fps)} />
        </Sequence>
      ) : null}

      {timeline.title ? (
        <Sequence durationInFrames={toFrames(timeline.title.duration_s, fps)}>
          <TitleOverlay title={timeline.title} frames={toFrames(timeline.title.duration_s, fps)} preset={preset} />
        </Sequence>
      ) : null}

      {(timeline.overlays ?? []).map((overlay, i) => (
        <Sequence
          key={`overlay-${i}`}
          from={toFrames(overlay.start_s, fps)}
          durationInFrames={toFrames(overlay.end_s - overlay.start_s, fps)}
        >
          <OverlayText overlay={overlay} frames={toFrames(overlay.end_s - overlay.start_s, fps)} preset={preset} />
        </Sequence>
      ))}

      {(timeline.effects ?? []).map((effect, i) =>
        effect.type === "flash" ? (
          <Sequence key={`fx-${i}`} from={toFrames(effect.at_s, fps)} durationInFrames={toFrames(effect.duration_s ?? 0.25, fps)}>
            <Flash frames={toFrames(effect.duration_s ?? 0.25, fps)} />
          </Sequence>
        ) : (
          <Sequence key={`fx-${i}`} from={durationInFrames - toFrames(effect.duration_s, fps)}>
            <FadeToBlack frames={toFrames(effect.duration_s, fps)} />
          </Sequence>
        ),
      )}

      {music?.src ? (
        <Audio
          src={staticFile(music.src)}
          volume={(f) =>
            musicGain *
            interpolate(f, [0, fps, durationInFrames - fps * 2, durationInFrames], [0, 1, 1, 0], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            })
          }
        />
      ) : null}
    </AbsoluteFill>
  );
};
