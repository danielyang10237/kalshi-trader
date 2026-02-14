#!/usr/bin/env python3
"""Test script to debug PlayByPlayV2 API"""

from nba_api.stats.endpoints import playbyplayv2
import traceback

game_id = "0021800001"  # 2018-10-16 PHI @ BOS

print(f"Testing PlayByPlayV2 for game_id: {game_id}")
print("=" * 60)

try:
    print("Step 1: Creating PlayByPlayV2 object...")
    pbp = playbyplayv2.PlayByPlayV2(game_id=game_id, timeout=30)
    print("  ✓ Object created successfully")
    
    print("\nStep 2: Checking available methods...")
    methods = [m for m in dir(pbp) if not m.startswith('_')]
    print(f"  Available methods: {methods[:10]}...")
    
    print("\nStep 3: Trying get_dict()...")
    try:
        raw = pbp.get_dict()
        print(f"  ✓ get_dict() succeeded")
        print(f"  Keys: {raw.keys()}")
        if 'resultSets' in raw:
            print(f"  resultSets count: {len(raw['resultSets'])}")
            if raw['resultSets']:
                rs = raw['resultSets'][0]
                print(f"  First resultSet keys: {rs.keys()}")
                print(f"  First resultSet name: {rs.get('name', 'N/A')}")
                print(f"  Headers: {rs.get('headers', [])[:5]}...")
                print(f"  Row count: {len(rs.get('rowSet', []))}")
    except Exception as e:
        print(f"  ✗ get_dict() failed: {e}")
        traceback.print_exc()
    
    print("\nStep 4: Trying get_data_frames()...")
    try:
        dfs = pbp.get_data_frames()
        print(f"  ✓ get_data_frames() succeeded")
        print(f"  Number of dataframes: {len(dfs)}")
        if dfs:
            print(f"  First df shape: {dfs[0].shape}")
            print(f"  First df columns: {dfs[0].columns.tolist()[:5]}...")
    except Exception as e:
        print(f"  ✗ get_data_frames() failed: {e}")
        traceback.print_exc()
    
    print("\nStep 5: Trying get_normalized_dict()...")
    try:
        norm = pbp.get_normalized_dict()
        print(f"  ✓ get_normalized_dict() succeeded")
        print(f"  Keys: {norm.keys()}")
    except Exception as e:
        print(f"  ✗ get_normalized_dict() failed: {e}")
        traceback.print_exc()

except Exception as e:
    print(f"\n✗ Failed to create PlayByPlayV2 object!")
    print(f"Error: {type(e).__name__}: {e}")
    print("\nFull traceback:")
    traceback.print_exc()

print("\n" + "=" * 60)
print("Test complete")
