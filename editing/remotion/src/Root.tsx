import React from "react";
import {Composition, staticFile} from "remotion";
import {Highlight, type HighlightProps} from "./Highlight";
import {layout, type Timeline} from "./timeline";

// render.py passes the staged timeline as --props. In Studio there are no props, so the
// most recently staged timeline is read from public/clips/current.json instead.
export const Root: React.FC = () => (
  <Composition
    id="Highlight"
    component={Highlight}
    defaultProps={{timeline: null} as HighlightProps}
    width={1920}
    height={1080}
    fps={24}
    durationInFrames={240}
    calculateMetadata={async ({props}) => {
      let timeline = props.timeline;
      if (!timeline) {
        const res = await fetch(staticFile("clips/current.json"));
        timeline = res.ok ? ((await res.json()) as Timeline) : null;
      }
      if (!timeline) return {props};
      return {
        props: {timeline},
        durationInFrames: layout(timeline).total,
        fps: timeline.output.fps,
        width: timeline.output.width,
        height: timeline.output.height,
      };
    }}
  />
);
