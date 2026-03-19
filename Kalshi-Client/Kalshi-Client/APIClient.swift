import Foundation

struct KalshiMarket: Codable {
    let ticker: String
    let status: String
    let yes_sub_title: String?
    let no_sub_title: String?
}

struct KalshiEvent: Identifiable, Codable {
    var id: String { event_ticker }
    let event_ticker: String
    let title: String
    let sub_title: String?
    let series_ticker: String?
    let markets: [KalshiMarket]?
}

struct EventsResponse: Codable {
    let events: [KalshiEvent]
    let cursor: String?
}

class APIClient {
    static let shared = APIClient()
    let base = "https://palisadescapital.co"

    func fetchNBAGames() async throws -> [KalshiEvent] {
        var all: [KalshiEvent] = []
        var cursor: String? = nil

        repeat {
            var components = URLComponents(string: "\(base)/api/events")!
            var items = [
                URLQueryItem(name: "series_ticker", value: "KXNBAGAME"),
                URLQueryItem(name: "with_nested_markets", value: "true"),
                URLQueryItem(name: "limit", value: "200"),
            ]
            if let c = cursor {
                items.append(URLQueryItem(name: "cursor", value: c))
            }
            components.queryItems = items

            let (data, _) = try await URLSession.shared.data(from: components.url!)
            let resp = try JSONDecoder().decode(EventsResponse.self, from: data)
            all.append(contentsOf: resp.events)
            cursor = (resp.cursor?.isEmpty == false) ? resp.cursor : nil
        } while cursor != nil

        // Only return events that have at least one active market
        return all.filter { event in
            event.markets?.contains(where: { $0.status == "active" }) ?? false
        }
    }
}
