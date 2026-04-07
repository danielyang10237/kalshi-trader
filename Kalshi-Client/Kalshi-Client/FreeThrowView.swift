import SwiftUI
import Combine

struct FreeThrowView: View {
    let team: String          // "home" or "away"
    let teamCode: String      // e.g. "LAL"
    let totalFTs: Int
    let color: Color
    @ObservedObject var ws: WebSocketManager
    let quarter: Int
    let onDone: (_ lastFTMissed: Bool) -> Void

    @State private var completed: Int = 0
    @State private var made: Int = 0
    @State private var missed: Int = 0
    @State private var ftEventCount: Int = 0  // track how many events we pushed
    @State private var lastWasMiss: Bool = false

    var body: some View {
        VStack(spacing: 20) {
            Text("\(teamCode) Free Throws")
                .font(.title2.bold())
                .foregroundColor(color)

            Text("FT \(completed + 1) of \(totalFTs)")
                .font(.headline)
                .foregroundColor(.secondary)

            HStack(spacing: 24) {
                VStack {
                    Text("\(made)")
                        .font(.system(size: 36, weight: .bold, design: .rounded))
                        .foregroundColor(.green)
                    Text("Made")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                VStack {
                    Text("\(missed)")
                        .font(.system(size: 36, weight: .bold, design: .rounded))
                        .foregroundColor(.red)
                    Text("Missed")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }

            if completed < totalFTs {
                HStack(spacing: 24) {
                    Button(action: {
                        if ws.pendingFtSigned > 0 { ws.pendingFtSigned -= 1 }
                        else if ws.pendingFtSigned < 0 { ws.pendingFtSigned += 1 }
                        ws.pushEvent(GameEvent(
                            type: "ft_made", team: team, quarter: quarter,
                            detail: "\(teamCode) FT Made"
                        ))
                        made += 1
                        completed += 1
                        ftEventCount += 1
                        lastWasMiss = false
                        checkDone()
                    }) {
                        Text("Made")
                            .font(.title3.bold())
                            .foregroundColor(.white)
                            .frame(width: 120, height: 60)
                            .background(Color.green)
                            .cornerRadius(12)
                    }

                    Button(action: {
                        if ws.pendingFtSigned > 0 { ws.pendingFtSigned -= 1 }
                        else if ws.pendingFtSigned < 0 { ws.pendingFtSigned += 1 }
                        ws.pushEvent(GameEvent(
                            type: "ft_miss", team: team, quarter: quarter,
                            detail: "\(teamCode) FT Miss"
                        ))
                        missed += 1
                        completed += 1
                        ftEventCount += 1
                        lastWasMiss = true
                        checkDone()
                    }) {
                        Text("Missed")
                            .font(.title3.bold())
                            .foregroundColor(.white)
                            .frame(width: 120, height: 60)
                            .background(Color.red)
                            .cornerRadius(12)
                    }
                }
            }

            Spacer()

            Button(action: {
                // Undo all FT events we pushed in this sequence
                ws.pendingFtSigned = 0
                ws.isDeadBall = false
                ws.undoLastN(ftEventCount)
                onDone(false)
            }) {
                HStack {
                    Image(systemName: "arrow.uturn.backward")
                    Text("Cancel & Undo")
                }
                .font(.callout)
                .foregroundColor(.red)
            }
            .padding(.bottom, 20)
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(.systemBackground))
    }

    private func checkDone() {
        if completed >= totalFTs {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                onDone(lastWasMiss)
            }
        }
    }
}
