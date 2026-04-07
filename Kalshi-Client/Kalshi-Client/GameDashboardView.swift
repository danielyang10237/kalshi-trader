import SwiftUI
import Combine

struct GameDashboardView: View {
    let gameId: String
    let homeTeam: String
    let awayTeam: String
    let alreadyStarted: Bool

    @StateObject private var ws: WebSocketManager
    @Environment(\.presentationMode) var presentationMode

    @State private var showFreeThrows = false
    @State private var ftTeam: String = "home"
    @State private var ftCount: Int = 0
    @State private var editingScore: String? = nil
    @State private var editScoreText: String = ""

    // Stats editing state
    @State private var showStatsEditor = false
    @State private var statsEditorTeam: String = "home"

    // Time taskbar state
    @State private var selectedTimeRange: String = "12-9"
    @State private var showTimer = false

    // Rebound prompt state
    @State private var showReboundPrompt = false
    @State private var reboundGroupId: String = ""
    @State private var reboundMissTeam: String = "home"  // team that missed

    // Trading params overlay
    @State private var showTradingParams = false

    // Timer-during-FTs state
    @State private var timerWasRunning = false

    // Timeout state
    @State private var showTimeout = false
    @State private var timeoutTeam: String = "home"
    @State private var timeoutSeconds: Int = 75
    @State private var timeoutRunning = false
    @State private var timeoutTask: Task<Void, Never>? = nil

    init(gameId: String, homeTeam: String, awayTeam: String, alreadyStarted: Bool) {
        self.gameId = gameId
        self.homeTeam = homeTeam
        self.awayTeam = awayTeam
        self.alreadyStarted = alreadyStarted
        _ws = StateObject(wrappedValue: WebSocketManager(
            gameId: gameId,
            homeTeam: homeTeam,
            awayTeam: awayTeam,
            alreadyStarted: alreadyStarted
        ))
    }

    private var side: String { ws.possession ?? "home" }
    private var otherSide: String { side == "home" ? "away" : "home" }
    private var teamCode: String { side == "home" ? homeTeam : awayTeam }
    private var otherTeamCode: String { side == "home" ? awayTeam : homeTeam }
    private var teamColor: Color { side == "home" ? .blue : .orange }
    private var otherColor: Color { side == "home" ? .orange : .blue }

    // MARK: - Backdrop

    private var backdropTeamCode: String? {
        guard let poss = ws.possession else { return nil }
        return poss == "home" ? homeTeam : awayTeam
    }

    // MARK: - Event Helpers

    private func makeEvent(type: String, team: String, value: Int? = nil, detail: String? = nil, groupId: String? = nil) -> GameEvent {
        GameEvent(type: type, team: team, value: value, quarter: ws.quarter, detail: detail, groupId: groupId)
    }

    private func switchPossession(groupId: String? = nil) {
        ws.pushEvent(makeEvent(type: "possession", team: otherSide, detail: "\(otherTeamCode) Ball", groupId: groupId))
    }

    // MARK: - Body

    var body: some View {
        HStack(spacing: 0) {
            // Main content area with background image
            GeometryReader { geo in
                if geo.size.width < geo.size.height {
                    VStack(spacing: 16) {
                        Image(systemName: "rotate.right")
                            .font(.system(size: 60))
                            .foregroundColor(.secondary)
                        Text("Rotate your phone to landscape")
                            .font(.title3)
                            .foregroundColor(.secondary)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if showFreeThrows {
                    FreeThrowView(
                        team: ftTeam,
                        teamCode: ftTeam == "home" ? homeTeam : awayTeam,
                        totalFTs: ftCount,
                        color: ftTeam == "home" ? .blue : .orange,
                        ws: ws,
                        quarter: ws.quarter,
                        onDone: { lastFTMissed in
                            showFreeThrows = false
                            ws.pendingFtSigned = 0
                            ws.isDeadBall = false
                            if lastFTMissed {
                                // Last FT missed → live ball, auto-resume clock
                                if timerWasRunning { ws.startGameTimer() }
                                reboundMissTeam = ftTeam
                                reboundGroupId = ws.events.last?.groupId ?? UUID().uuidString
                                showReboundPrompt = true
                            } else {
                                // Last FT made → clock stays stopped, manual play required
                                ws.pushEvent(makeEvent(type: "possession", team: ftTeam == "home" ? "away" : "home",
                                                       detail: "\(ftTeam == "home" ? awayTeam : homeTeam) Ball"))
                            }
                        }
                    )
                } else if showTimeout {
                    timeoutView()
                } else if ws.possession == nil {
                    tipoffView()
                } else {
                    mainContent()
                }
            }
            .background(
                Group {
                    if let code = backdropTeamCode, let img = UIImage(named: code) {
                        Image(uiImage: img)
                            .resizable()
                            .aspectRatio(contentMode: .fill)
                            .opacity(0.9)
                            .animation(.easeInOut(duration: 0.4), value: code)
                    } else {
                        Color(.systemBackground)
                    }
                }
            )
            .clipped()

            // Right sidebar — outside the background image
            if ws.possession != nil && !showFreeThrows && !showTimeout {
                rightSidebar()
            }
        }
        .ignoresSafeArea()
        .navigationBarHidden(true)
        .navigationBarBackButtonHidden(true)
        .toolbar(.hidden, for: .navigationBar)
        .onAppear {
            ws.connect()
            if ws.timeRange == "5-0" || ws.quarter >= 5 {
                showTimer = true
                selectedTimeRange = ws.timeRange
            }
        }
        .onDisappear { ws.disconnect() }
        .onChange(of: ws.isStopped) { stopped in
            if stopped { presentationMode.wrappedValue.dismiss() }
        }
    }

    // MARK: - Tipoff

    @ViewBuilder
    private func tipoffView() -> some View {
        HStack(spacing: 0) {
            Button(action: {
                ws.pushEvent(makeEvent(type: "possession", team: "home", detail: "\(homeTeam) Ball"))
            }) {
                VStack(spacing: 12) {
                    Text(homeTeam)
                        .font(.system(size: 36, weight: .bold))
                        .foregroundColor(.blue)
                    Text("Tap for possession")
                        .font(.callout)
                        .foregroundColor(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            Rectangle().fill(Color.gray.opacity(0.3)).frame(width: 2)

            Button(action: {
                ws.pushEvent(makeEvent(type: "possession", team: "away", detail: "\(awayTeam) Ball"))
            }) {
                VStack(spacing: 12) {
                    Text(awayTeam)
                        .font(.system(size: 36, weight: .bold))
                        .foregroundColor(.orange)
                    Text("Tap for possession")
                        .font(.callout)
                        .foregroundColor(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        .overlay(
            Text("TIPOFF")
                .font(.caption.bold())
                .foregroundColor(.secondary)
                .padding(.horizontal, 12)
                .padding(.vertical, 4)
                .background(Color(.systemBackground).opacity(0.9))
                .cornerRadius(8),
            alignment: .top
        )
        .padding(.top, 8)
    }

    // MARK: - Time ranges per quarter

    private var isOT: Bool { ws.quarter >= 5 }

    private var timeRanges: [String] {
        if ws.quarter <= 3 {
            return ["12-9", "9-6", "6-3", "3-0"]
        } else if ws.quarter == 4 {
            return ["12-9", "9-7", "7-5", "5-0"]
        } else {
            return []
        }
    }

    private func selectQuarter(_ q: Int) {
        ws.pushEvent(makeEvent(type: "quarter", team: side, value: q, detail: q >= 5 ? "OT" : "Q\(q)"))
        if q >= 5 {
            selectedTimeRange = ""
            showTimer = true
            ws.timerSeconds = 300
            ws.stopGameTimer()
            ws.toggleGameTimer()
        } else {
            selectedTimeRange = "12-9"
            ws.pushEvent(makeEvent(type: "time_range", team: side, detail: "12-9"))
            showTimer = false
            ws.stopGameTimer()
        }
    }

    private func selectTimeRange(_ range: String) {
        selectedTimeRange = range
        ws.pushEvent(makeEvent(type: "time_range", team: side, detail: range))
        if ws.quarter == 4 && range == "5-0" {
            showTimer = true
            ws.timerSeconds = 300
            ws.stopGameTimer()
            ws.toggleGameTimer()
        } else {
            showTimer = false
            ws.stopGameTimer()
        }
    }

    private var timerDisplay: String {
        let m = ws.timerSeconds / 60
        let s = ws.timerSeconds % 60
        return String(format: "%d:%02d", m, s)
    }

    // MARK: - Main Dashboard

    private let cardBg = Color(.systemBackground).opacity(0.85)

    @ViewBuilder
    private func mainContent() -> some View {
            VStack(spacing: 6) {
                // Top bar: time controls (left) + scoreboard (right), matched height
                HStack(alignment: .top, spacing: 8) {
                    timeTaskbar()
                        .padding(6)
                        .frame(maxHeight: .infinity)
                        .background(cardBg)
                        .cornerRadius(12)

                    // Scoreboard
                    VStack(spacing: 4) {
                        HStack(spacing: 8) {
                            Button(action: {
                                statsEditorTeam = "away"
                                showStatsEditor = true
                            }) {
                                Image(systemName: "gearshape.fill")
                                    .font(.system(size: 18))
                                    .foregroundColor(.orange.opacity(0.6))
                            }
                            .buttonStyle(.plain)

                            Button(action: {
                                editScoreText = "\(ws.awayStats.score)"
                                editingScore = "away"
                            }) {
                                VStack(spacing: 1) {
                                    Text(awayTeam)
                                        .font(.system(size: 10, weight: .semibold))
                                        .foregroundColor(.orange)
                                    Text("\(ws.awayStats.score)")
                                        .font(.system(size: 26, weight: .heavy, design: .rounded))
                                        .foregroundColor(.primary)
                                }
                                .frame(width: 70)
                                .background(Color.orange.opacity(side == "away" ? 0.2 : 0.05))
                                .cornerRadius(8)
                            }
                            .buttonStyle(.plain)

                            Text("—")
                                .font(.system(size: 14))
                                .foregroundColor(.secondary)

                            Button(action: {
                                editScoreText = "\(ws.homeStats.score)"
                                editingScore = "home"
                            }) {
                                VStack(spacing: 1) {
                                    Text(homeTeam)
                                        .font(.system(size: 10, weight: .semibold))
                                        .foregroundColor(.blue)
                                    Text("\(ws.homeStats.score)")
                                        .font(.system(size: 26, weight: .heavy, design: .rounded))
                                        .foregroundColor(.primary)
                                }
                                .frame(width: 70)
                                .background(Color.blue.opacity(side == "home" ? 0.2 : 0.05))
                                .cornerRadius(8)
                            }
                            .buttonStyle(.plain)

                            Button(action: {
                                statsEditorTeam = "home"
                                showStatsEditor = true
                            }) {
                                Image(systemName: "gearshape.fill")
                                    .font(.system(size: 18))
                                    .foregroundColor(.blue.opacity(0.6))
                            }
                            .buttonStyle(.plain)
                        }

                        timeoutIndicators()
                    }
                    .padding(6)
                    .frame(maxHeight: .infinity)
                    .background(cardBg)
                    .cornerRadius(12)

                    // Trading settings
                    tradingBlock()
                        .padding(6)
                        .frame(maxHeight: .infinity)
                        .background(cardBg)
                        .cornerRadius(12)
                }
                .fixedSize(horizontal: false, vertical: true)

                // Possession indicator
                HStack {
                    Circle().fill(teamColor).frame(width: 10, height: 10)
                    Text("\(teamCode) Possession")
                        .font(.system(size: 14, weight: .bold))
                        .foregroundColor(teamColor)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 4)
                .background(cardBg)
                .cornerRadius(8)

                // Shot buttons — two columns, each with Made/Miss centered
                HStack(spacing: 0) {
                    // Left column: +2
                    HStack(spacing: 10) {
                        squareShotBtn("+2", subtitle: "Made", color: teamColor) {
                            let gid = UUID().uuidString
                            ws.isDeadBall = true
                            ws.pushEvent(makeEvent(type: "fg_made", team: side, value: 2, detail: "\(teamCode) +2 Made", groupId: gid))
                            switchPossession(groupId: gid)
                        }
                        squareShotBtn("+2", subtitle: "Miss", color: .gray) {
                            let gid = UUID().uuidString
                            ws.isDeadBall = false
                            ws.pushEvent(makeEvent(type: "fg_miss", team: side, value: 2, detail: "\(teamCode) +2 Miss", groupId: gid))
                            reboundGroupId = gid
                            reboundMissTeam = side
                            showReboundPrompt = true
                        }
                    }
                    .frame(maxWidth: .infinity)

                    // Right column: +3
                    HStack(spacing: 10) {
                        squareShotBtn("+3", subtitle: "Made", color: teamColor) {
                            let gid = UUID().uuidString
                            ws.isDeadBall = true
                            ws.pushEvent(makeEvent(type: "fg_made", team: side, value: 3, detail: "\(teamCode) +3 Made", groupId: gid))
                            switchPossession(groupId: gid)
                        }
                        squareShotBtn("+3", subtitle: "Miss", color: .gray) {
                            let gid = UUID().uuidString
                            ws.isDeadBall = false
                            ws.pushEvent(makeEvent(type: "fg_miss", team: side, value: 3, detail: "\(teamCode) +3 Miss", groupId: gid))
                            reboundGroupId = gid
                            reboundMissTeam = side
                            showReboundPrompt = true
                        }
                    }
                    .frame(maxWidth: .infinity)
                }

                Spacer()

                // Foul buttons — pinned to bottom
                HStack(spacing: 24) {
                    foulBtn("And1\n2pt", color: .green) {
                        let gid = UUID().uuidString
                        ws.isDeadBall = true
                        ws.pendingFtSigned = (side == "home" ? 1 : -1) * 1
                        ws.pushEvent(makeEvent(type: "fg_made", team: side, value: 2, detail: "\(teamCode) And1 2pt", groupId: gid))
                        ws.pushEvent(makeEvent(type: "foul", team: otherSide, detail: "\(otherTeamCode) Foul", groupId: gid))
                        timerWasRunning = ws.timerRunning
                        if ws.timerRunning { ws.stopGameTimer() }
                        ftTeam = side; ftCount = 1; showFreeThrows = true
                    }
                    foulBtn("Foul\n2pt", color: .orange) {
                        let gid = UUID().uuidString
                        ws.isDeadBall = true
                        ws.pendingFtSigned = (side == "home" ? 1 : -1) * 2
                        ws.pushEvent(makeEvent(type: "foul", team: otherSide, detail: "\(otherTeamCode) Foul 2pt", groupId: gid))
                        timerWasRunning = ws.timerRunning
                        if ws.timerRunning { ws.stopGameTimer() }
                        ftTeam = side; ftCount = 2; showFreeThrows = true
                    }
                    foulBtn("And1\n3pt", color: .green) {
                        let gid = UUID().uuidString
                        ws.isDeadBall = true
                        ws.pendingFtSigned = (side == "home" ? 1 : -1) * 1
                        ws.pushEvent(makeEvent(type: "fg_made", team: side, value: 3, detail: "\(teamCode) And1 3pt", groupId: gid))
                        ws.pushEvent(makeEvent(type: "foul", team: otherSide, detail: "\(otherTeamCode) Foul", groupId: gid))
                        timerWasRunning = ws.timerRunning
                        if ws.timerRunning { ws.stopGameTimer() }
                        ftTeam = side; ftCount = 1; showFreeThrows = true
                    }
                    foulBtn("Foul\n3pt", color: .orange) {
                        let gid = UUID().uuidString
                        ws.isDeadBall = true
                        ws.pendingFtSigned = (side == "home" ? 1 : -1) * 3
                        ws.pushEvent(makeEvent(type: "foul", team: otherSide, detail: "\(otherTeamCode) Foul 3pt", groupId: gid))
                        timerWasRunning = ws.timerRunning
                        if ws.timerRunning { ws.stopGameTimer() }
                        ftTeam = side; ftCount = 3; showFreeThrows = true
                    }
                    foulBtn("Off\nFoul", color: .red) {
                        let gid = UUID().uuidString
                        ws.isDeadBall = true
                        ws.pushEvent(makeEvent(type: "off_foul", team: side, detail: "\(teamCode) Off Foul", groupId: gid))
                        switchPossession(groupId: gid)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 6)

                // Last action display
                if !ws.lastAction.isEmpty {
                    Text(ws.lastAction)
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundColor(.black)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 6)
                        .background(Color.white.opacity(0.9))
                        .cornerRadius(8)
                        .padding(.bottom, 10)
                }
            }
            .frame(maxWidth: .infinity)
            .padding(.top, 10)
        .overlay(
            Group {
                if showReboundPrompt {
                    reboundPromptView()
                }
                if editingScore != nil {
                    scoreEditOverlay()
                }
                if showStatsEditor {
                    statsEditorOverlay()
                }
                if showTradingParams {
                    tradingParamsOverlay()
                }
            }
        )
    }

    // MARK: - Right Sidebar

    @ViewBuilder
    private func rightSidebar() -> some View {
        VStack(spacing: 8) {
            Spacer()

            // Switch possession
            Button(action: { ws.isDeadBall = false; switchPossession() }) {
                VStack(spacing: 4) {
                    Image(systemName: "arrow.left.arrow.right.circle.fill")
                        .font(.system(size: 28))
                    Text("Switch")
                        .font(.system(size: 9, weight: .semibold))
                }
                .foregroundColor(.white)
                .frame(width: 66, height: 48)
                .background(otherColor)
                .cornerRadius(10)
            }

            // Steal
            Button(action: {
                let gid = UUID().uuidString
                ws.isDeadBall = false
                ws.pushEvent(makeEvent(type: "steal", team: otherSide, detail: "\(otherTeamCode) Steal", groupId: gid))
                ws.pushEvent(makeEvent(type: "turnover", team: side, detail: "\(teamCode) TOV", groupId: gid))
                switchPossession(groupId: gid)
            }) {
                VStack(spacing: 4) {
                    Image(systemName: "hand.raised.fill")
                        .font(.system(size: 20))
                    Text("Steal")
                        .font(.system(size: 9, weight: .semibold))
                }
                .foregroundColor(.white)
                .frame(width: 66, height: 48)
                .background(otherColor)
                .cornerRadius(10)
            }

            // Turnover
            Button(action: {
                let gid = UUID().uuidString
                ws.isDeadBall = false
                ws.pushEvent(makeEvent(type: "turnover", team: side, detail: "\(teamCode) TOV", groupId: gid))
                switchPossession(groupId: gid)
            }) {
                VStack(spacing: 4) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 20))
                    Text("TOV")
                        .font(.system(size: 9, weight: .semibold))
                }
                .foregroundColor(.white)
                .frame(width: 66, height: 48)
                .background(Color.red.opacity(0.7))
                .cornerRadius(10)
            }

            // Timeout
            Button(action: { startTimeout(team: side) }) {
                VStack(spacing: 4) {
                    Image(systemName: "timer")
                        .font(.system(size: 20))
                    Text("T/O")
                        .font(.system(size: 9, weight: .semibold))
                }
                .foregroundColor(.white)
                .frame(width: 66, height: 48)
                .background(Color.yellow.opacity(0.8))
                .cornerRadius(10)
            }

            Spacer()

            // Undo
            Button(action: { ws.undoLast() }) {
                VStack(spacing: 4) {
                    Image(systemName: "arrow.uturn.backward")
                        .font(.system(size: 18))
                    Text("Undo")
                        .font(.system(size: 9, weight: .semibold))
                }
                .foregroundColor(.white)
                .frame(width: 66, height: 40)
                .background(Color.gray)
                .cornerRadius(10)
            }

            // Stop
            Button(action: { ws.sendStop() }) {
                Text("Stop")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundColor(.white)
                    .frame(width: 66, height: 28)
                    .background(Color.red)
                    .cornerRadius(8)
            }
        }
        .frame(width: 82)
        .padding(.vertical, 6)
        .padding(.trailing, 4)
        .background(Color(.systemBackground))
    }

    // MARK: - Rebound Prompt

    @ViewBuilder
    private func reboundPromptView() -> some View {
        let missTeamCode = reboundMissTeam == "home" ? homeTeam : awayTeam
        let otherRebTeam = reboundMissTeam == "home" ? "away" : "home"
        let otherRebCode = reboundMissTeam == "home" ? awayTeam : homeTeam
        let missColor: Color = reboundMissTeam == "home" ? .blue : .orange
        let otherRebColor: Color = reboundMissTeam == "home" ? .orange : .blue

        ZStack {
            Color.black.opacity(0.5).ignoresSafeArea()

            VStack(spacing: 16) {
                Text("Rebound?")
                    .font(.system(size: 18, weight: .bold))
                    .foregroundColor(.primary)

                HStack(spacing: 20) {
                    Button(action: {
                        ws.pushEvent(makeEvent(type: "oreb", team: reboundMissTeam,
                                               detail: "\(missTeamCode) O-Reb", groupId: reboundGroupId))
                        showReboundPrompt = false
                    }) {
                        VStack(spacing: 4) {
                            Image(systemName: "arrow.up.circle.fill")
                                .font(.system(size: 28))
                            Text("O-Reb")
                                .font(.system(size: 13, weight: .bold))
                            Text(missTeamCode)
                                .font(.system(size: 11))
                        }
                        .foregroundColor(.white)
                        .frame(width: 100, height: 80)
                        .background(missColor)
                        .cornerRadius(12)
                    }

                    Button(action: {
                        ws.pushEvent(makeEvent(type: "dreb", team: otherRebTeam,
                                               detail: "\(otherRebCode) D-Reb", groupId: reboundGroupId))
                        ws.pushEvent(makeEvent(type: "possession", team: otherRebTeam,
                                               detail: "\(otherRebCode) Ball", groupId: reboundGroupId))
                        showReboundPrompt = false
                    }) {
                        VStack(spacing: 4) {
                            Image(systemName: "sportscourt.fill")
                                .font(.system(size: 28))
                            Text("D-Reb")
                                .font(.system(size: 13, weight: .bold))
                            Text(otherRebCode)
                                .font(.system(size: 11))
                        }
                        .foregroundColor(.white)
                        .frame(width: 100, height: 80)
                        .background(otherRebColor)
                        .cornerRadius(12)
                    }
                }
            }
            .padding(24)
            .background(Color(.systemBackground))
            .cornerRadius(16)
            .shadow(radius: 20)
        }
    }

    // MARK: - Time Taskbar

    @ViewBuilder
    private func timeTaskbar() -> some View {
        VStack(spacing: 6) {
            // Top row: Back + Quarter pills
            HStack(spacing: 6) {
                Button(action: { presentationMode.wrappedValue.dismiss() }) {
                    Image(systemName: "chevron.left")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(.secondary)
                        .frame(width: 40, height: 36)
                        .background(Color.gray.opacity(0.2))
                        .cornerRadius(8)
                }
                .buttonStyle(.plain)

                Rectangle().fill(Color.gray.opacity(0.3)).frame(width: 1, height: 24)

                ForEach(1...4, id: \.self) { q in
                    Button(action: { selectQuarter(q) }) {
                        Text("Q\(q)")
                            .font(.system(size: 14, weight: .bold))
                            .foregroundColor(ws.quarter == q ? .white : .secondary)
                            .frame(width: 48, height: 36)
                            .background(ws.quarter == q ? Color.blue : Color.gray.opacity(0.2))
                            .cornerRadius(8)
                    }
                    .buttonStyle(.plain)
                }

                Button(action: { selectQuarter(5) }) {
                    Text("OT")
                        .font(.system(size: 14, weight: .bold))
                        .foregroundColor(isOT ? .white : .secondary)
                        .frame(width: 48, height: 36)
                        .background(isOT ? Color.red : Color.gray.opacity(0.2))
                        .cornerRadius(8)
                }
                .buttonStyle(.plain)
            }

            // Second row: Time range pills
            if !timeRanges.isEmpty {
                HStack(spacing: 6) {
                    ForEach(timeRanges, id: \.self) { range in
                        Button(action: { selectTimeRange(range) }) {
                            Text(range)
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundColor(selectedTimeRange == range ? .white : .secondary)
                                .frame(width: 60, height: 36)
                                .background(selectedTimeRange == range ? Color.green.opacity(0.8) : Color.gray.opacity(0.2))
                                .cornerRadius(8)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            // Timer row (Q4 5-0 or OT)
            if showTimer {
                HStack(spacing: 6) {
                    timerAdjustBtn("-10", delta: -10)
                    timerAdjustBtn("-5", delta: -5)
                    timerAdjustBtn("-2", delta: -2)

                    Text(timerDisplay)
                        .font(.system(size: 16, weight: .heavy, design: .monospaced))
                        .foregroundColor(ws.timerSeconds <= 60 ? .red : .primary)
                        .frame(width: 50)

                    timerAdjustBtn("+2", delta: 2)
                    timerAdjustBtn("+5", delta: 5)
                    timerAdjustBtn("+10", delta: 10)

                    Rectangle().fill(Color.gray.opacity(0.3)).frame(width: 1, height: 16)

                    Button(action: { ws.toggleGameTimer() }) {
                        Image(systemName: ws.timerRunning ? "pause.fill" : "play.fill")
                            .font(.system(size: 13))
                            .foregroundColor(.white)
                            .frame(width: 30, height: 24)
                            .background(ws.timerRunning ? Color.orange : Color.green)
                            .cornerRadius(5)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    @ViewBuilder
    private func timerAdjustBtn(_ label: String, delta: Int) -> some View {
        Button(action: {
            ws.adjustTimer(delta: delta)
        }) {
            Text(label)
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(delta < 0 ? .red : .green)
                .frame(width: 28, height: 22)
                .background(Color.gray.opacity(0.15))
                .cornerRadius(4)
        }
        .buttonStyle(.plain)
    }

    // MARK: - Timeout

    private func startTimeout(team: String) {
        timeoutTeam = team
        timeoutSeconds = 75
        showTimeout = true
        timeoutRunning = true
        ws.isDeadBall = true
        timeoutTask = Task {
            while !Task.isCancelled && timeoutSeconds > 0 {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                if Task.isCancelled { break }
                timeoutSeconds -= 1
            }
            timeoutRunning = false
        }
    }

    private func cancelTimeout() {
        timeoutTask?.cancel()
        timeoutTask = nil
        timeoutRunning = false
        showTimeout = false
        ws.isDeadBall = false
    }

    private func endTimeout() {
        timeoutTask?.cancel()
        timeoutTask = nil
        timeoutRunning = false
        showTimeout = false
        ws.isDeadBall = false
        let code = timeoutTeam == "home" ? homeTeam : awayTeam
        ws.pushEvent(makeEvent(type: "timeout", team: timeoutTeam, detail: "\(code) Timeout"))
    }

    private func timeoutsRemaining(_ team: String) -> Int {
        let used = team == "home" ? ws.homeStats.timeouts_used : ws.awayStats.timeouts_used
        return max(0, 7 - used)
    }

    private func q4TimeoutsRemaining(_ team: String) -> Int {
        let q4Key = "4"
        let used = team == "home"
            ? (ws.homeStats.period_fouls[q4Key] ?? 0)  // reusing for simplicity — actually need timeout tracking per quarter
            : (ws.awayStats.period_fouls[q4Key] ?? 0)
        // Count timeouts used in Q4+ from events
        let q4Timeouts = ws.events.filter { $0.type == "timeout" && $0.team == team && $0.quarter >= 4 }.count
        return max(0, 4 - q4Timeouts)
    }

    private var timeoutDisplay: String {
        let m = timeoutSeconds / 60
        let s = timeoutSeconds % 60
        return String(format: "%d:%02d", m, s)
    }

    @ViewBuilder
    private func timeoutView() -> some View {
        let team = timeoutTeam
        let code = team == "home" ? homeTeam : awayTeam
        let color: Color = team == "home" ? .blue : .orange

        VStack(spacing: 20) {
            Spacer()

            Text("\(code) TIMEOUT")
                .font(.system(size: 28, weight: .bold))
                .foregroundColor(color)

            Text(timeoutDisplay)
                .font(.system(size: 64, weight: .heavy, design: .monospaced))
                .foregroundColor(timeoutSeconds <= 10 ? .red : .primary)

            HStack(spacing: 4) {
                ForEach(0..<7, id: \.self) { i in
                    Image(systemName: "timer")
                        .font(.system(size: 14))
                        .foregroundColor(i < timeoutsRemaining(team) ? color : Color.gray.opacity(0.3))
                }
            }

            if ws.quarter >= 4 {
                HStack(spacing: 2) {
                    Text("Q4:")
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                    ForEach(0..<4, id: \.self) { i in
                        Image(systemName: "timer")
                            .font(.system(size: 12))
                            .foregroundColor(i < q4TimeoutsRemaining(team) ? color : Color.gray.opacity(0.3))
                    }
                }
            }

            Spacer()

            HStack(spacing: 24) {
                Button(action: { cancelTimeout() }) {
                    Text("Cancel")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(.white)
                        .frame(width: 140, height: 50)
                        .background(Color.gray)
                        .cornerRadius(12)
                }

                Button(action: { endTimeout() }) {
                    Text("End Timeout")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(.white)
                        .frame(width: 140, height: 50)
                        .background(color)
                        .cornerRadius(12)
                }
            }

            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    @ViewBuilder
    private func timeoutIndicators() -> some View {
        HStack(spacing: 16) {
            VStack(spacing: 2) {
                HStack(spacing: 3) {
                    ForEach(0..<7, id: \.self) { i in
                        Image(systemName: "timer")
                            .font(.system(size: 8))
                            .foregroundColor(i < timeoutsRemaining("away") ? .orange : Color.gray.opacity(0.25))
                    }
                }
                if ws.quarter >= 4 {
                    HStack(spacing: 3) {
                        Text("Q4")
                            .font(.system(size: 7))
                            .foregroundColor(.secondary)
                        ForEach(0..<4, id: \.self) { i in
                            Image(systemName: "timer")
                                .font(.system(size: 7))
                                .foregroundColor(i < q4TimeoutsRemaining("away") ? .orange : Color.gray.opacity(0.25))
                        }
                    }
                }
            }

            VStack(spacing: 2) {
                HStack(spacing: 3) {
                    ForEach(0..<7, id: \.self) { i in
                        Image(systemName: "timer")
                            .font(.system(size: 8))
                            .foregroundColor(i < timeoutsRemaining("home") ? .blue : Color.gray.opacity(0.25))
                    }
                }
                if ws.quarter >= 4 {
                    HStack(spacing: 3) {
                        Text("Q4")
                            .font(.system(size: 7))
                            .foregroundColor(.secondary)
                        ForEach(0..<4, id: \.self) { i in
                            Image(systemName: "timer")
                                .font(.system(size: 7))
                                .foregroundColor(i < q4TimeoutsRemaining("home") ? .blue : Color.gray.opacity(0.25))
                        }
                    }
                }
            }
        }
    }

    // MARK: - Trading Block

    private var pnlDisplay: String {
        guard let cents = ws.pnlCents else { return "—" }
        let dollars = Double(cents) / 100.0
        return String(format: "%@$%.2f", dollars >= 0 ? "+" : "-", abs(dollars))
    }

    private var pnlColor: Color {
        guard let cents = ws.pnlCents else { return .secondary }
        return cents >= 0 ? .green : .red
    }

    private var exposureDisplay: String {
        let dollars = Double(ws.totalExposureCents) / 100.0
        return String(format: "$%.2f", dollars)
    }

    @ViewBuilder
    private func tradingBlock() -> some View {
        VStack(spacing: 6) {
            // Header with gear icon
            HStack(spacing: 4) {
                Text("Trading")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundColor(.yellow)
                Spacer()
                Button(action: { showTradingParams = true }) {
                    Image(systemName: "slider.horizontal.3")
                        .font(.system(size: 14))
                        .foregroundColor(.yellow.opacity(0.7))
                }
                .buttonStyle(.plain)
            }

            // Enable toggle
            HStack(spacing: 6) {
                Text("Enabled")
                    .font(.system(size: 10, weight: .medium))
                    .foregroundColor(.secondary)
                Spacer()
                Button(action: { ws.toggleTrading() }) {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(ws.tradingEnabled ? Color.yellow : Color.gray.opacity(0.4))
                        .frame(width: 34, height: 18)
                        .overlay(
                            Circle()
                                .fill(Color.white)
                                .frame(width: 14, height: 14)
                                .offset(x: ws.tradingEnabled ? 7 : -7),
                            alignment: .center
                        )
                        .animation(.easeInOut(duration: 0.15), value: ws.tradingEnabled)
                }
                .buttonStyle(.plain)
            }

            // P&L + Exposure column
            VStack(spacing: 4) {
                HStack(spacing: 4) {
                    Text("P&L")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundColor(.secondary)
                    Spacer()
                    Text(pnlDisplay)
                        .font(.system(size: 14, weight: .heavy, design: .monospaced))
                        .foregroundColor(pnlColor)
                }
                HStack(spacing: 4) {
                    Text("Exposure")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundColor(.secondary)
                    Spacer()
                    Text(exposureDisplay)
                        .font(.system(size: 12, weight: .bold, design: .monospaced))
                        .foregroundColor(.white)
                }
            }
            .padding(6)
            .background(Color.gray.opacity(0.15))
            .cornerRadius(6)

            // Position
            if ws.homePosition != 0 {
                HStack(spacing: 4) {
                    Text("Pos")
                        .font(.system(size: 9, weight: .medium))
                        .foregroundColor(.secondary)
                    Spacer()
                    Text("\(ws.homePosition > 0 ? "+" : "")\(ws.homePosition)")
                        .font(.system(size: 11, weight: .bold, design: .monospaced))
                        .foregroundColor(ws.homePosition > 0 ? .green : .red)
                }
            }

            if !ws.engineLive {
                Text("Engine offline")
                    .font(.system(size: 8))
                    .foregroundColor(.gray)
            }
        }
        .frame(width: 110)
    }

    // MARK: - Trading Params Editor

    @ViewBuilder
    private func tradingParamsOverlay() -> some View {
        ZStack {
            Color.black.opacity(0.5).ignoresSafeArea()
                .onTapGesture { showTradingParams = false }

            VStack(spacing: 12) {
                HStack {
                    Text("Trading Parameters")
                        .font(.system(size: 16, weight: .bold))
                        .foregroundColor(.yellow)
                    Spacer()
                    Button(action: { showTradingParams = false }) {
                        Image(systemName: "xmark.circle.fill")
                            .font(.system(size: 22))
                            .foregroundColor(.secondary)
                    }
                }

                tradingParamRow("Min Size", key: "min_size", value: ws.tradingParams.minSize, suffix: "ct")
                tradingParamRow("Max Size", key: "max_size", value: ws.tradingParams.maxSize, suffix: "ct")
                tradingParamRow("Max Position", key: "max_position", value: ws.tradingParams.maxPosition, suffix: "ct")
                tradingParamRowDollars("Max Exposure", key: "max_exposure", cents: ws.tradingParams.maxExposure)
                tradingParamRowDouble("Delta Scale", key: "delta_scale", value: ws.tradingParams.deltaScale, step: 0.1)
                tradingParamRowDouble("Min Delta", key: "min_delta", value: ws.tradingParams.minDelta, step: 0.005)
                tradingParamRowDouble("Full Scale", key: "delta_full_scale", value: ws.tradingParams.deltaFullScale, step: 0.01)
                tradingParamRow("Aggression", key: "aggression", value: ws.tradingParams.aggression, suffix: "\u{00A2}")
            }
            .padding(20)
            .background(Color(.systemBackground))
            .cornerRadius(16)
            .shadow(radius: 20)
            .frame(maxWidth: 320)
        }
    }

    @ViewBuilder
    private func tradingParamRow(_ label: String, key: String, value: Int, suffix: String) -> some View {
        HStack(spacing: 8) {
            Text(label)
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(.secondary)
                .frame(width: 100, alignment: .leading)

            Button(action: {
                let newVal = max(0, value - 1)
                ws.tradingParams.update(key: key, intValue: newVal)
                ws.updateTradingParam(key: key, value: newVal)
            }) {
                Image(systemName: "minus")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundColor(.white)
                    .frame(width: 26, height: 26)
                    .background(Color.red.opacity(0.7))
                    .cornerRadius(6)
            }
            .buttonStyle(.plain)

            Text("\(value)")
                .font(.system(size: 13, weight: .bold, design: .monospaced))
                .frame(width: 50)
                .multilineTextAlignment(.center)

            Button(action: {
                let newVal = value + 1
                ws.tradingParams.update(key: key, intValue: newVal)
                ws.updateTradingParam(key: key, value: newVal)
            }) {
                Image(systemName: "plus")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundColor(.white)
                    .frame(width: 26, height: 26)
                    .background(Color.yellow.opacity(0.8))
                    .cornerRadius(6)
            }
            .buttonStyle(.plain)

            Text(suffix)
                .font(.system(size: 9))
                .foregroundColor(.secondary)
                .frame(width: 16, alignment: .leading)
        }
    }

    @ViewBuilder
    private func tradingParamRowDollars(_ label: String, key: String, cents: Int) -> some View {
        let dollars = cents / 100
        HStack(spacing: 8) {
            Text(label)
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(.secondary)
                .frame(width: 100, alignment: .leading)

            Button(action: {
                let newCents = max(0, cents - 10000)
                ws.tradingParams.maxExposure = newCents
                ws.updateTradingParam(key: key, value: newCents)
            }) {
                Image(systemName: "minus")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundColor(.white)
                    .frame(width: 26, height: 26)
                    .background(Color.red.opacity(0.7))
                    .cornerRadius(6)
            }
            .buttonStyle(.plain)

            Text("$\(dollars)")
                .font(.system(size: 13, weight: .bold, design: .monospaced))
                .frame(width: 50)
                .multilineTextAlignment(.center)

            Button(action: {
                let newCents = cents + 10000
                ws.tradingParams.maxExposure = newCents
                ws.updateTradingParam(key: key, value: newCents)
            }) {
                Image(systemName: "plus")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundColor(.white)
                    .frame(width: 26, height: 26)
                    .background(Color.yellow.opacity(0.8))
                    .cornerRadius(6)
            }
            .buttonStyle(.plain)

            Text("$")
                .font(.system(size: 9))
                .foregroundColor(.secondary)
                .frame(width: 16, alignment: .leading)
        }
    }

    @ViewBuilder
    private func tradingParamRowDouble(_ label: String, key: String, value: Double, step: Double) -> some View {
        HStack(spacing: 8) {
            Text(label)
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(.secondary)
                .frame(width: 100, alignment: .leading)

            Button(action: {
                let newVal = max(0, value - step)
                ws.tradingParams.update(key: key, doubleValue: newVal)
                ws.updateTradingParam(key: key, value: newVal)
            }) {
                Image(systemName: "minus")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundColor(.white)
                    .frame(width: 26, height: 26)
                    .background(Color.red.opacity(0.7))
                    .cornerRadius(6)
            }
            .buttonStyle(.plain)

            Text(String(format: "%.3f", value))
                .font(.system(size: 13, weight: .bold, design: .monospaced))
                .frame(width: 50)
                .multilineTextAlignment(.center)

            Button(action: {
                let newVal = value + step
                ws.tradingParams.update(key: key, doubleValue: newVal)
                ws.updateTradingParam(key: key, value: newVal)
            }) {
                Image(systemName: "plus")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundColor(.white)
                    .frame(width: 26, height: 26)
                    .background(Color.yellow.opacity(0.8))
                    .cornerRadius(6)
            }
            .buttonStyle(.plain)

            Text("")
                .frame(width: 16)
        }
    }

    // MARK: - Buttons

    @ViewBuilder
    private func squareShotBtn(_ title: String, subtitle: String, color: Color, width: CGFloat = 112, height: CGFloat = 100, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 4) {
                Text(title)
                    .font(.system(size: 36, weight: .heavy))
                Text(subtitle)
                    .font(.system(size: 16, weight: .semibold))
            }
            .foregroundColor(.white)
            .frame(width: width, height: height)
            .background(color)
            .cornerRadius(12)
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(Color.white.opacity(0.6), lineWidth: 1.5)
            )
        }
    }

    @ViewBuilder
    private func shotBtn(_ label: String, color: Color, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label)
                .font(.system(size: 15, weight: .bold))
                .foregroundColor(.white)
                .frame(maxWidth: .infinity)
                .frame(height: 40)
                .background(color)
                .cornerRadius(10)
        }
    }

    @ViewBuilder
    private func foulBtn(_ label: String, color: Color = .purple, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label)
                .font(.system(size: 13, weight: .semibold))
                .multilineTextAlignment(.center)
                .foregroundColor(.white)
                .frame(maxWidth: .infinity)
                .frame(width: UIScreen.main.bounds.width * 0.13)
                .frame(height: 64)
                .background(color.opacity(0.85))
                .cornerRadius(8)
                .overlay(
                    RoundedRectangle(cornerRadius: 8)
                        .stroke(Color.white.opacity(0.6), lineWidth: 1)
                )
        }
    }

    // MARK: - Score Edit Overlay

    @ViewBuilder
    private func scoreEditOverlay() -> some View {
        let team = editingScore ?? "home"
        let code = team == "home" ? homeTeam : awayTeam
        let color: Color = team == "home" ? .blue : .orange
        let currentScore = team == "home" ? ws.homeStats.score : ws.awayStats.score

        ZStack {
            Color.black.opacity(0.5).ignoresSafeArea()
                .onTapGesture { editingScore = nil }

            VStack(spacing: 16) {
                Text("\(code) Score")
                    .font(.system(size: 18, weight: .bold))
                    .foregroundColor(color)

                TextField("Score", text: $editScoreText)
                    .keyboardType(.numberPad)
                    .font(.system(size: 36, weight: .heavy, design: .monospaced))
                    .multilineTextAlignment(.center)
                    .frame(width: 120)
                    .padding(8)
                    .background(Color.gray.opacity(0.15))
                    .cornerRadius(10)

                HStack(spacing: 16) {
                    Button("Cancel") { editingScore = nil }
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(.white)
                        .frame(width: 90, height: 40)
                        .background(Color.gray)
                        .cornerRadius(10)

                    Button("Save") {
                        if let newScore = Int(editScoreText) {
                            let delta = newScore - currentScore
                            if delta != 0 {
                                ws.pushEvent(makeEvent(
                                    type: "score_adjust",
                                    team: team,
                                    value: delta,
                                    detail: "\(code) score \(delta > 0 ? "+" : "")\(delta)"
                                ))
                            }
                        }
                        editingScore = nil
                    }
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(.white)
                    .frame(width: 90, height: 40)
                    .background(color)
                    .cornerRadius(10)
                }
            }
            .padding(24)
            .background(Color(.systemBackground))
            .cornerRadius(16)
            .shadow(radius: 20)
        }
    }

    // MARK: - Stats Editor Overlay

    @ViewBuilder
    private func statsEditorOverlay() -> some View {
        let team = statsEditorTeam
        let code = team == "home" ? homeTeam : awayTeam
        let color: Color = team == "home" ? .blue : .orange
        let stats = team == "home" ? ws.homeStats : ws.awayStats

        ZStack {
            Color.black.opacity(0.5).ignoresSafeArea()
                .onTapGesture { showStatsEditor = false }

            VStack(spacing: 12) {
                HStack {
                    Text("\(code) Stats")
                        .font(.system(size: 18, weight: .bold))
                        .foregroundColor(color)
                    Spacer()
                    Button(action: { showStatsEditor = false }) {
                        Image(systemName: "xmark.circle.fill")
                            .font(.system(size: 22))
                            .foregroundColor(.secondary)
                    }
                }

                let rows: [(String, String, Int)] = [
                    ("FGM", "fgm", stats.fgm),
                    ("FGA", "fga", stats.fga),
                    ("3PM", "fg3m", stats.fg3m),
                    ("3PA", "fg3a", stats.fg3a),
                    ("FTM", "ftm", stats.ftm),
                    ("FTA", "fta", stats.fta),
                    ("OREB", "oreb", stats.oreb),
                    ("DREB", "dreb", stats.dreb),
                    ("TOV", "tov", stats.tov),
                    ("STL", "stl", stats.stl),
                    ("PF", "pf", stats.pf),
                    ("T/O Used", "timeouts_used", stats.timeouts_used),
                ]

                // Two-column grid
                let leftCol = Array(rows.prefix(6))
                let rightCol = Array(rows.suffix(6))

                HStack(alignment: .top, spacing: 16) {
                    VStack(spacing: 6) {
                        ForEach(leftCol, id: \.1) { row in
                            statAdjustRow(label: row.0, key: row.1, value: row.2, team: team, color: color)
                        }
                    }
                    VStack(spacing: 6) {
                        ForEach(rightCol, id: \.1) { row in
                            statAdjustRow(label: row.0, key: row.1, value: row.2, team: team, color: color)
                        }
                    }
                }
            }
            .padding(20)
            .background(Color(.systemBackground))
            .cornerRadius(16)
            .shadow(radius: 20)
            .frame(maxWidth: 380)
        }
    }

    @ViewBuilder
    private func statAdjustRow(label: String, key: String, value: Int, team: String, color: Color) -> some View {
        let code = team == "home" ? homeTeam : awayTeam
        HStack(spacing: 8) {
            Text(label)
                .font(.system(size: 11, weight: .medium))
                .foregroundColor(.secondary)
                .frame(width: 50, alignment: .leading)

            Button(action: {
                ws.pushEvent(makeEvent(type: "stat_adjust", team: team, value: -1, detail: key))
            }) {
                Image(systemName: "minus")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundColor(.white)
                    .frame(width: 26, height: 26)
                    .background(Color.red.opacity(0.7))
                    .cornerRadius(6)
            }
            .buttonStyle(.plain)

            Text("\(value)")
                .font(.system(size: 14, weight: .bold, design: .monospaced))
                .frame(width: 32)
                .multilineTextAlignment(.center)

            Button(action: {
                ws.pushEvent(makeEvent(type: "stat_adjust", team: team, value: 1, detail: key))
            }) {
                Image(systemName: "plus")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundColor(.white)
                    .frame(width: 26, height: 26)
                    .background(color)
                    .cornerRadius(6)
            }
            .buttonStyle(.plain)
        }
    }
}
