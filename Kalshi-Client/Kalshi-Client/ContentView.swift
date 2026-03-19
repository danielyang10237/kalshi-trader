//
//  ContentView.swift
//  Kalshi-Client
//
//  Created by Admin on 3/6/26.
//

import SwiftUI
import Combine

@MainActor
class GamesViewModel: ObservableObject {
    @Published var events: [KalshiEvent] = []
    @Published var isLoading = false
    @Published var error: String?

    func fetchGames() {
        isLoading = true
        error = nil
        Task {
            do {
                let games = try await APIClient.shared.fetchNBAGames()
                self.events = games
            } catch {
                self.error = error.localizedDescription
            }
            self.isLoading = false
        }
    }
}

struct ContentView: View {
    @StateObject private var vm = GamesViewModel()

    var body: some View {
        NavigationStack {
            Group {
                if vm.isLoading {
                    ProgressView("Loading games...")
                } else if let error = vm.error {
                    VStack(spacing: 16) {
                        Image(systemName: "exclamationmark.triangle")
                            .font(.largeTitle)
                            .foregroundColor(.orange)
                        Text(error)
                            .multilineTextAlignment(.center)
                            .foregroundColor(.secondary)
                        Button("Retry") { vm.fetchGames() }
                    }
                    .padding()
                } else if vm.events.isEmpty {
                    Text("No active NBA games")
                        .foregroundColor(.secondary)
                } else {
                    List(vm.events) { event in
                        NavigationLink(destination: GameDetailView(event: event)) {
                            Text(event.title)
                                .font(.headline)
                                .foregroundColor(.primary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.vertical, 6)
                        }
                    }
                    .refreshable { vm.fetchGames() }
                }
            }
            .navigationTitle("NBA Games")
        }
        .onAppear { vm.fetchGames() }
    }
}

#Preview {
    ContentView()
}
