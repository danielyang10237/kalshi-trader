"use client";

import { useState } from "react";
import { seedBook, clearBook } from "@/lib/api";

interface Props {
  onBookChanged?: () => void;
}

export default function SimControls({ onBookChanged }: Props) {
  const [midpoint, setMidpoint] = useState(50);
  const [spread, setSpread] = useState(4);
  const [depth, setDepth] = useState(100);
  const [levels, setLevels] = useState(5);
  const [loading, setLoading] = useState(false);

  async function handleSeed() {
    setLoading(true);
    try {
      await seedBook(midpoint, spread, depth, levels);
      onBookChanged?.();
    } catch (e) {
      console.error("Seed failed:", e);
    }
    setLoading(false);
  }

  async function handleClear() {
    setLoading(true);
    try {
      await clearBook();
      onBookChanged?.();
    } catch (e) {
      console.error("Clear failed:", e);
    }
    setLoading(false);
  }

  function applyPreset(name: string) {
    switch (name) {
      case "tight":
        setMidpoint(50);
        setSpread(2);
        setDepth(200);
        setLevels(5);
        break;
      case "wide":
        setMidpoint(50);
        setSpread(10);
        setDepth(50);
        setLevels(8);
        break;
      case "deep":
        setMidpoint(50);
        setSpread(4);
        setDepth(500);
        setLevels(10);
        break;
    }
  }

  return (
    <div className="border border-gray-700 rounded-lg p-4">
      <h3 className="text-sm font-semibold mb-3">Book Controls</h3>

      {/* Presets */}
      <div className="flex gap-2 mb-4">
        <button
          onClick={() => applyPreset("tight")}
          className="px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded"
        >
          Tight (2c)
        </button>
        <button
          onClick={() => applyPreset("wide")}
          className="px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded"
        >
          Wide (10c)
        </button>
        <button
          onClick={() => applyPreset("deep")}
          className="px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded"
        >
          Deep Book
        </button>
      </div>

      {/* Sliders */}
      <div className="space-y-3">
        <div>
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>Midpoint</span>
            <span className="font-mono">{midpoint}c</span>
          </div>
          <input
            type="range"
            min={5}
            max={95}
            value={midpoint}
            onChange={(e) => setMidpoint(Number(e.target.value))}
            className="w-full h-1.5 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
          />
        </div>

        <div>
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>Spread</span>
            <span className="font-mono">{spread}c</span>
          </div>
          <input
            type="range"
            min={2}
            max={20}
            value={spread}
            onChange={(e) => setSpread(Number(e.target.value))}
            className="w-full h-1.5 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
          />
        </div>

        <div>
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>Depth/Level</span>
            <span className="font-mono">{depth}</span>
          </div>
          <input
            type="range"
            min={10}
            max={1000}
            step={10}
            value={depth}
            onChange={(e) => setDepth(Number(e.target.value))}
            className="w-full h-1.5 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
          />
        </div>

        <div>
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>Levels</span>
            <span className="font-mono">{levels}</span>
          </div>
          <input
            type="range"
            min={1}
            max={15}
            value={levels}
            onChange={(e) => setLevels(Number(e.target.value))}
            className="w-full h-1.5 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
          />
        </div>
      </div>

      {/* Action buttons */}
      <div className="flex gap-2 mt-4">
        <button
          onClick={handleSeed}
          disabled={loading}
          className="flex-1 px-3 py-2 text-sm bg-blue-600 hover:bg-blue-500 disabled:bg-gray-600 rounded font-medium"
        >
          {loading ? "..." : "Seed Book"}
        </button>
        <button
          onClick={handleClear}
          disabled={loading}
          className="px-3 py-2 text-sm bg-red-700 hover:bg-red-600 disabled:bg-gray-600 rounded font-medium"
        >
          Clear
        </button>
      </div>
    </div>
  );
}
