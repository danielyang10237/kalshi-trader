import SwiftUI
import Combine

private struct GameStatusCheck: Codable {
    let home_team: String?
}

struct GameDetailView: View {
    let event: KalshiEvent

    @State private var isLoading = true
    @State private var gameStarted = false
    @State private var navigateToDashboard = false

    private var teamCodes: (home: String, away: String) {
        // sub_title format: "AWAY at HOME (date)"
        guard let sub = event.sub_title,
              let parenIdx = sub.firstIndex(of: "(") else {
            return ("???", "???")
        }
        let teamPart = sub[sub.startIndex..<parenIdx].trimmingCharacters(in: .whitespaces)
        let parts = teamPart.components(separatedBy: " at ")
        guard parts.count == 2 else { return ("???", "???") }
        return (home: parts[1].trimmingCharacters(in: .whitespaces),
                away: parts[0].trimmingCharacters(in: .whitespaces))
    }

    var body: some View {
        VStack(spacing: 24) {
            Text(event.title)
                .font(.title2)
                .fontWeight(.bold)
                .multilineTextAlignment(.center)
                .padding(.horizontal)

            if let sub = event.sub_title {
                Text(sub)
                    .font(.subheadline)
                    .foregroundColor(.secondary)
            }

            if isLoading {
                ProgressView("Checking game status...")
            } else {
                NavigationLink(destination: GameDashboardView(
                    gameId: event.event_ticker,
                    homeTeam: teamCodes.home,
                    awayTeam: teamCodes.away,
                    alreadyStarted: gameStarted
                ), isActive: $navigateToDashboard) {
                    EmptyView()
                }

                Button(action: { navigateToDashboard = true }) {
                    Text(gameStarted ? "Resume Game" : "Start Game")
                        .font(.headline)
                        .foregroundColor(.white)
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(gameStarted ? Color.green : Color.blue)
                        .cornerRadius(12)
                }
                .padding(.horizontal, 40)
            }

            Spacer()
        }
        .padding(.top, 40)
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { checkGameStatus() }
    }

    private func checkGameStatus() {
        isLoading = true
        let urlString = "https://palisadescapital.co/nba/games/\(event.event_ticker)"
        guard let url = URL(string: urlString) else {
            isLoading = false
            return
        }
        Task {
            do {
                let (data, _) = try await URLSession.shared.data(from: url)
                let state = try JSONDecoder().decode(GameStatusCheck.self, from: data)
                gameStarted = state.home_team != nil
            } catch {
                gameStarted = false
            }
            isLoading = false
        }
    }
}
