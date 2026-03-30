"use client";

import { useEffect, useRef, useState } from "react";
import { getPBP, type PBPPlay } from "@/lib/api";

interface Props {
  homeTeam: string;
  awayTeam: string;
}

export default function SimGameFeed({ homeTeam, awayTeam }: Props) {
  const [plays, setPlays] = useState<PBPPlay[]>([]);
  const [score, setScore] = useState({ away_score: 0, home_score: 0, period: "", clock: "" });
  const feedRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const data = await getPBP(30);
        setPlays(data.plays);
        setScore(data.score);
      } catch {}
    }, 500);
    return () => clearInterval(interval);
  }, []);

  // Auto-scroll to bottom
  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTop = feedRef.current.scrollHeight;
    }
  }, [plays]);

  return (
    <div className="border border-gray-700 rounded-lg overflow-hidden">
      {/* Scoreboard */}
      <div className="bg-gray-800 px-4 py-3 border-b border-gray-700">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="text-center">
              <div className="text-[10px] text-gray-400 uppercase">{awayTeam}</div>
              <div className="text-2xl font-bold tabular-nums">{score.away_score}</div>
            </div>
            <div className="text-gray-500 text-xs">@</div>
            <div className="text-center">
              <div className="text-[10px] text-gray-400 uppercase">{homeTeam}</div>
              <div className="text-2xl font-bold tabular-nums">{score.home_score}</div>
            </div>
          </div>
          <div className="text-right">
            <div className="text-xs text-gray-400">{score.period}</div>
            <div className="text-sm font-mono text-gray-300">{score.clock}</div>
          </div>
        </div>
      </div>

      {/* Play-by-play feed */}
      <div ref={feedRef} className="max-h-[250px] overflow-y-auto p-2 space-y-1">
        {plays.length === 0 ? (
          <div className="text-xs text-gray-500 text-center py-4">
            Start replay to see plays
          </div>
        ) : (
          plays.map((play, i) => (
            <div
              key={i}
              className={`text-xs px-2 py-1 rounded ${
                play.scoring
                  ? "bg-green-900/30 border-l-2 border-green-500"
                  : "border-l-2 border-transparent"
              }`}
            >
              <div className="flex items-baseline gap-2">
                <span className="text-gray-500 font-mono text-[10px] shrink-0">
                  {play.clock}
                </span>
                <span className={play.scoring ? "text-green-300" : "text-gray-300"}>
                  {play.text}
                </span>
              </div>
              {play.scoring && (
                <div className="text-[10px] text-gray-500 ml-12 mt-0.5">
                  {awayTeam} {play.away_score} - {homeTeam} {play.home_score}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
