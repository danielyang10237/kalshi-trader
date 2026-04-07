import Foundation
import Combine

// MARK: - Data Models

struct GameEvent: Codable, Identifiable {
    let id: String
    let timestamp: Double
    let type: String
    // "fg_made", "fg_miss", "ft_made", "ft_miss",
    // "steal", "turnover", "oreb", "dreb",
    // "foul", "off_foul", "timeout",
    // "possession", "quarter", "time_range", "stop"
    let team: String       // "home" or "away"
    let value: Int?        // points for fg (2/3), quarter number for quarter event
    let quarter: Int       // quarter at time of event
    let detail: String?    // human-readable, e.g. "+2 Made", "Steal"
    let groupId: String    // events with same groupId are undone together

    init(type: String, team: String, value: Int? = nil, quarter: Int, detail: String? = nil, groupId: String? = nil) {
        self.id = UUID().uuidString
        self.timestamp = Date().timeIntervalSince1970
        self.type = type
        self.team = team
        self.value = value
        self.quarter = quarter
        self.detail = detail
        self.groupId = groupId ?? UUID().uuidString
    }
}

struct TeamStats: Codable {
    var score: Int = 0
    var fgm: Int = 0
    var fga: Int = 0
    var fg3m: Int = 0
    var fg3a: Int = 0
    var ftm: Int = 0
    var fta: Int = 0
    var oreb: Int = 0
    var dreb: Int = 0
    var tov: Int = 0
    var stl: Int = 0
    var pf: Int = 0
    var timeouts_used: Int = 0
    var period_fouls: [String: Int] = [:]  // "1" -> count, using String keys for JSON compat

    static var zero: TeamStats { TeamStats() }
}

struct GameSnapshot: Codable {
    let game_id: String
    let timestamp: Double
    let home_team: String
    let away_team: String
    var possession: String?
    var quarter: Int
    var time_range: String
    var timer_seconds: Int
    var stopped: Bool
    var pending_ft_signed: Int
    var is_dead_ball: Bool
    var home: TeamStats
    var away: TeamStats
    var events: [GameEvent]
    var last_action: String
}

struct TradingParamsLocal {
    var minSize: Int = 5
    var maxSize: Int = 50
    var maxPosition: Int = 200
    var maxExposure: Int = 50000   // cents
    var deltaScale: Double = 0.6
    var minDelta: Double = 0.01
    var deltaFullScale: Double = 0.08
    var aggression: Int = 1

    mutating func update(key: String, intValue: Int) {
        switch key {
        case "min_size": minSize = intValue
        case "max_size": maxSize = intValue
        case "max_position": maxPosition = intValue
        case "max_exposure": maxExposure = intValue
        case "aggression": aggression = intValue
        default: break
        }
    }

    mutating func update(key: String, doubleValue: Double) {
        switch key {
        case "delta_scale": deltaScale = doubleValue
        case "min_delta": minDelta = doubleValue
        case "delta_full_scale": deltaFullScale = doubleValue
        default: break
        }
    }
}

// For decoding server's initial state on connect
private struct ServerMessage: Codable {
    let game_id: String?
    let timestamp: Double?
    let home_team: String?
    let away_team: String?
    let possession: String?
    let quarter: Int?
    let time_range: String?
    let timer_seconds: Int?
    let stopped: Bool?
    let home: TeamStats?
    let away: TeamStats?
    let events: [GameEvent]?
    let last_action: String?
    // Legacy fields for backwards compat on first connect
    let home_score: Int?
    let away_score: Int?
}

// MARK: - WebSocketManager

@MainActor
class WebSocketManager: ObservableObject {
    // Published state
    @Published var possession: String? = nil
    @Published var quarter: Int = 1
    @Published var timeRange: String = "12-9"
    @Published var timerSeconds: Int = 300
    @Published var timerRunning: Bool = false
    @Published var isConnected = false
    @Published var isStopped = false
    @Published var homeStats: TeamStats = .zero
    @Published var awayStats: TeamStats = .zero
    @Published var lastAction: String = ""
    @Published var events: [GameEvent] = []
    @Published var pendingFtSigned: Int = 0
    @Published var isDeadBall: Bool = false

    // Trading state (from feed WebSocket)
    @Published var tradingEnabled: Bool = false
    @Published var homePosition: Int = 0
    @Published var pnlCents: Int? = nil
    @Published var totalExposureCents: Int = 0
    @Published var tradingParams = TradingParamsLocal()
    @Published var engineLive: Bool = false

    var homeScore: Int { homeStats.score }
    var awayScore: Int { awayStats.score }

    let gameId: String
    let homeTeam: String
    let awayTeam: String

    private var webSocketTask: URLSessionWebSocketTask?
    private var feedTask: URLSessionWebSocketTask?
    private var isSetup: Bool
    private var timerTask: Task<Void, Never>?
    private var snapshotTimestamp: Double = 0
    private let baseURL = "https://palisadescapital.co"

    // Persistence key
    private var persistKey: String { "game_events_\(gameId)" }
    private var persistMetaKey: String { "game_meta_\(gameId)" }

    init(gameId: String, homeTeam: String, awayTeam: String, alreadyStarted: Bool) {
        self.gameId = gameId
        self.homeTeam = homeTeam
        self.awayTeam = awayTeam
        self.isSetup = alreadyStarted
        restoreFromLocal()
    }

    // MARK: - Connection

    func connect() {
        let urlString = "wss://palisadescapital.co/nba/ws/input/\(gameId)"
        guard let url = URL(string: urlString) else { return }
        let session = URLSession(configuration: .default)
        webSocketTask = session.webSocketTask(with: url)
        webSocketTask?.resume()
        isConnected = true
        listen()
        connectFeed()

        if !isSetup {
            sendSetup()
        }
    }

    func disconnect() {
        webSocketTask?.cancel(with: .goingAway, reason: nil)
        webSocketTask = nil
        feedTask?.cancel(with: .goingAway, reason: nil)
        feedTask = nil
        isConnected = false
        stopGameTimer()
    }

    private func sendSetup() {
        let msg: [String: String] = [
            "action": "setup",
            "home_team": homeTeam,
            "away_team": awayTeam
        ]
        sendJSON(msg)
        isSetup = true
    }

    // MARK: - Event Engine

    func pushEvent(_ event: GameEvent) {
        events.append(event)
        recomputeState()
        lastAction = event.detail ?? event.type
        snapshotTimestamp = Date().timeIntervalSince1970
        sendSnapshot()
        persistLocally()
    }

    func undoLast() {
        // Find the last non-timer event and remove all events in its group
        guard let lastSignificant = events.last(where: { $0.type != "time_range" }) else { return }
        let gid = lastSignificant.groupId
        events.removeAll { $0.groupId == gid }
        recomputeState()
        if let last = events.last(where: { $0.type != "time_range" }) {
            lastAction = "Undo → \(last.detail ?? last.type)"
        } else {
            lastAction = ""
        }
        snapshotTimestamp = Date().timeIntervalSince1970
        sendSnapshot(deltaReset: true)
        persistLocally()
    }

    func undoLastN(_ count: Int) {
        for _ in 0..<count {
            if let last = events.lastIndex(where: { $0.type != "time_range" }) {
                events.remove(at: last)
            }
        }
        recomputeState()
        if let last = events.last(where: { $0.type != "time_range" }) {
            lastAction = "Undo → \(last.detail ?? last.type)"
        } else {
            lastAction = ""
        }
        snapshotTimestamp = Date().timeIntervalSince1970
        sendSnapshot(deltaReset: true)
        persistLocally()
    }

    // MARK: - State Computation

    func recomputeState() {
        var home = TeamStats.zero
        var away = TeamStats.zero
        var currentPossession: String? = nil
        var currentQuarter = 1
        var currentTimeRange = "12-9"

        for event in events {
            let isHome = event.team == "home"

            switch event.type {
            case "fg_made":
                let pts = event.value ?? 2
                if isHome {
                    home.score += pts
                    home.fgm += 1
                    home.fga += 1
                    if pts == 3 { home.fg3m += 1; home.fg3a += 1 }
                } else {
                    away.score += pts
                    away.fgm += 1
                    away.fga += 1
                    if pts == 3 { away.fg3m += 1; away.fg3a += 1 }
                }

            case "fg_miss":
                let pts = event.value ?? 2
                if isHome {
                    home.fga += 1
                    if pts == 3 { home.fg3a += 1 }
                } else {
                    away.fga += 1
                    if pts == 3 { away.fg3a += 1 }
                }

            case "ft_made":
                if isHome {
                    home.ftm += 1; home.fta += 1; home.score += 1
                } else {
                    away.ftm += 1; away.fta += 1; away.score += 1
                }

            case "ft_miss":
                if isHome {
                    home.fta += 1
                } else {
                    away.fta += 1
                }

            case "steal":
                if isHome { home.stl += 1 } else { away.stl += 1 }

            case "turnover":
                if isHome { home.tov += 1 } else { away.tov += 1 }

            case "oreb":
                if isHome { home.oreb += 1 } else { away.oreb += 1 }

            case "dreb":
                if isHome { home.dreb += 1 } else { away.dreb += 1 }

            case "foul":
                let qKey = String(event.quarter)
                if isHome {
                    home.pf += 1
                    home.period_fouls[qKey, default: 0] += 1
                } else {
                    away.pf += 1
                    away.period_fouls[qKey, default: 0] += 1
                }

            case "off_foul":
                let qKey = String(event.quarter)
                if isHome {
                    home.pf += 1; home.tov += 1
                    home.period_fouls[qKey, default: 0] += 1
                } else {
                    away.pf += 1; away.tov += 1
                    away.period_fouls[qKey, default: 0] += 1
                }

            case "timeout":
                if isHome { home.timeouts_used += 1 } else { away.timeouts_used += 1 }

            case "possession":
                currentPossession = event.team

            case "quarter":
                currentQuarter = event.value ?? currentQuarter

            case "time_range":
                currentTimeRange = event.detail ?? currentTimeRange

            case "score_adjust":
                // Direct score adjustment (detail = "+N" or "-N")
                let delta = event.value ?? 0
                if isHome { home.score += delta } else { away.score += delta }

            case "stat_adjust":
                // Adjust a specific stat (detail = stat key, value = delta)
                let delta = event.value ?? 0
                let key = event.detail ?? ""
                if isHome {
                    switch key {
                    case "fgm": home.fgm += delta
                    case "fga": home.fga += delta
                    case "fg3m": home.fg3m += delta
                    case "fg3a": home.fg3a += delta
                    case "ftm": home.ftm += delta
                    case "fta": home.fta += delta
                    case "oreb": home.oreb += delta
                    case "dreb": home.dreb += delta
                    case "tov": home.tov += delta
                    case "stl": home.stl += delta
                    case "pf": home.pf += delta
                    case "timeouts_used": home.timeouts_used += delta
                    default: break
                    }
                } else {
                    switch key {
                    case "fgm": away.fgm += delta
                    case "fga": away.fga += delta
                    case "fg3m": away.fg3m += delta
                    case "fg3a": away.fg3a += delta
                    case "ftm": away.ftm += delta
                    case "fta": away.fta += delta
                    case "oreb": away.oreb += delta
                    case "dreb": away.dreb += delta
                    case "tov": away.tov += delta
                    case "stl": away.stl += delta
                    case "pf": away.pf += delta
                    case "timeouts_used": away.timeouts_used += delta
                    default: break
                    }
                }

            default:
                break
            }
        }

        homeStats = home
        awayStats = away
        possession = currentPossession
        quarter = currentQuarter
        timeRange = currentTimeRange
    }

    // MARK: - Timer

    func startGameTimer() {
        stopGameTimer()
        timerRunning = true
        timerTask = Task {
            while !Task.isCancelled && timerSeconds > 0 {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                if Task.isCancelled { break }
                timerSeconds -= 1
                snapshotTimestamp = Date().timeIntervalSince1970
                sendSnapshot()
                persistLocally()
            }
            timerRunning = false
        }
    }

    func stopGameTimer() {
        timerTask?.cancel()
        timerTask = nil
        timerRunning = false
    }

    func toggleGameTimer() {
        if timerRunning {
            stopGameTimer()
        } else {
            startGameTimer()
        }
    }

    func adjustTimer(delta: Int) {
        timerSeconds = min(300, max(0, timerSeconds + delta))
        snapshotTimestamp = Date().timeIntervalSince1970
        sendSnapshot()
        persistLocally()
    }

    // MARK: - Snapshot

    private func buildSnapshot() -> GameSnapshot {
        GameSnapshot(
            game_id: gameId,
            timestamp: snapshotTimestamp,
            home_team: homeTeam,
            away_team: awayTeam,
            possession: possession,
            quarter: quarter,
            time_range: timeRange,
            timer_seconds: timerSeconds,
            stopped: isStopped,
            pending_ft_signed: pendingFtSigned,
            is_dead_ball: isDeadBall,
            home: homeStats,
            away: awayStats,
            events: events,
            last_action: lastAction
        )
    }

    func sendSnapshot(deltaReset: Bool = false) {
        let snapshot = buildSnapshot()
        guard let data = try? JSONEncoder().encode(snapshot),
              var dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        dict["action"] = "snapshot"
        if deltaReset {
            dict["delta_reset"] = true
        }
        guard let jsonData = try? JSONSerialization.data(withJSONObject: dict),
              let str = String(data: jsonData, encoding: .utf8) else { return }
        webSocketTask?.send(.string(str)) { _ in }
    }

    func sendStop() {
        isStopped = true
        let msg: [String: Any] = ["action": "stop", "game_id": gameId]
        guard let data = try? JSONSerialization.data(withJSONObject: msg),
              let str = String(data: data, encoding: .utf8) else { return }
        webSocketTask?.send(.string(str)) { _ in }
        clearLocal()
    }

    // MARK: - Network

    private func sendJSON(_ dict: [String: String]) {
        guard let data = try? JSONSerialization.data(withJSONObject: dict),
              let str = String(data: data, encoding: .utf8) else { return }
        webSocketTask?.send(.string(str)) { _ in }
    }

    private func listen() {
        webSocketTask?.receive { [weak self] result in
            Task { @MainActor in
                guard let self = self else { return }
                switch result {
                case .success(let message):
                    switch message {
                    case .string(let text):
                        self.handleMessage(text)
                    case .data(let data):
                        if let text = String(data: data, encoding: .utf8) {
                            self.handleMessage(text)
                        }
                    @unknown default:
                        break
                    }
                    self.listen()
                case .failure(_):
                    self.isConnected = false
                    if !self.isStopped {
                        try? await Task.sleep(nanoseconds: 2_000_000_000)
                        self.connect()
                    }
                }
            }
        }
    }

    private func handleMessage(_ text: String) {
        guard let data = text.data(using: .utf8) else { return }

        // Try to decode as server snapshot
        guard let msg = try? JSONDecoder().decode(ServerMessage.self, from: data) else { return }

        if msg.stopped == true {
            isStopped = true
            disconnect()
            clearLocal()
            return
        }

        // Reconnect sync: compare timestamps
        let serverTimestamp = msg.timestamp ?? 0
        if serverTimestamp > snapshotTimestamp, let serverEvents = msg.events, !serverEvents.isEmpty {
            // Server has newer state — adopt it
            events = serverEvents
            timerSeconds = msg.timer_seconds ?? timerSeconds
            recomputeState()
            lastAction = msg.last_action ?? ""
            snapshotTimestamp = serverTimestamp
            persistLocally()
        } else if !events.isEmpty {
            // Local is newer — push our state to server
            sendSnapshot()
        }
    }

    // MARK: - Feed WebSocket (trading state)

    private func connectFeed() {
        let urlString = "wss://palisadescapital.co/nba/ws/feed/\(gameId)"
        guard let url = URL(string: urlString) else { return }
        feedTask = URLSession(configuration: .default).webSocketTask(with: url)
        feedTask?.resume()
        listenFeed()
    }

    private func listenFeed() {
        feedTask?.receive { [weak self] result in
            Task { @MainActor in
                guard let self = self else { return }
                switch result {
                case .success(let message):
                    switch message {
                    case .string(let text): self.handleFeedMessage(text)
                    case .data(let data):
                        if let text = String(data: data, encoding: .utf8) {
                            self.handleFeedMessage(text)
                        }
                    @unknown default: break
                    }
                    self.listenFeed()
                case .failure(_):
                    if !self.isStopped {
                        try? await Task.sleep(nanoseconds: 3_000_000_000)
                        self.connectFeed()
                    }
                }
            }
        }
    }

    private func handleFeedMessage(_ text: String) {
        guard let data = text.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }

        // If no trader key, engine is not running
        guard let trader = json["trader"] as? [String: Any] else {
            engineLive = false
            return
        }
        engineLive = true

        if let params = trader["params"] as? [String: Any] {
            tradingEnabled = params["enabled"] as? Bool ?? false
            tradingParams.minSize = params["min_size"] as? Int ?? tradingParams.minSize
            tradingParams.maxSize = params["max_size"] as? Int ?? tradingParams.maxSize
            tradingParams.maxPosition = params["max_position"] as? Int ?? tradingParams.maxPosition
            tradingParams.maxExposure = params["max_exposure"] as? Int ?? tradingParams.maxExposure
            tradingParams.deltaScale = params["delta_scale"] as? Double ?? tradingParams.deltaScale
            tradingParams.minDelta = params["min_delta"] as? Double ?? tradingParams.minDelta
            tradingParams.deltaFullScale = params["delta_full_scale"] as? Double ?? tradingParams.deltaFullScale
            tradingParams.aggression = params["aggression"] as? Int ?? tradingParams.aggression
        }

        let position = trader["home_position"] as? Int ?? 0
        let cost = trader["home_cost"] as? Int ?? 0
        let bestBid = trader["home_best_bid"] as? Int
        homePosition = position
        totalExposureCents = trader["total_exposure"] as? Int ?? cost

        if position == 0 {
            pnlCents = -cost
        } else if let bid = bestBid {
            pnlCents = -cost + position * bid
        } else {
            pnlCents = nil
        }
    }

    // MARK: - Trading Controls

    func toggleTrading() {
        let endpoint = tradingEnabled ? "disable" : "enable"
        guard let url = URL(string: "\(baseURL)/nba/trading/\(endpoint)/\(gameId)") else { return }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        tradingEnabled.toggle() // optimistic
        URLSession.shared.dataTask(with: request) { _, _, _ in }.resume()
    }

    func updateTradingParam(key: String, value: Any) {
        guard let url = URL(string: "\(baseURL)/nba/trading/params/\(gameId)") else { return }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: [key: value])
        URLSession.shared.dataTask(with: request) { _, _, _ in }.resume()
    }

    // MARK: - Local Persistence

    func persistLocally() {
        guard let eventsData = try? JSONEncoder().encode(events) else { return }
        UserDefaults.standard.set(eventsData, forKey: persistKey)
        let meta: [String: Any] = [
            "quarter": quarter,
            "timeRange": timeRange,
            "timerSeconds": timerSeconds,
            "timestamp": snapshotTimestamp,
            "lastAction": lastAction
        ]
        UserDefaults.standard.set(meta, forKey: persistMetaKey)
    }

    private func restoreFromLocal() {
        guard let eventsData = UserDefaults.standard.data(forKey: persistKey),
              let restored = try? JSONDecoder().decode([GameEvent].self, from: eventsData) else { return }
        events = restored
        recomputeState()

        if let meta = UserDefaults.standard.dictionary(forKey: persistMetaKey) {
            timerSeconds = meta["timerSeconds"] as? Int ?? 300
            snapshotTimestamp = meta["timestamp"] as? Double ?? 0
            lastAction = meta["lastAction"] as? String ?? ""
            // timeRange and quarter are recomputed from events
        }
    }

    private func clearLocal() {
        UserDefaults.standard.removeObject(forKey: persistKey)
        UserDefaults.standard.removeObject(forKey: persistMetaKey)
    }
}
