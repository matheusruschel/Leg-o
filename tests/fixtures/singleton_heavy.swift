import Foundation

class OrderManager {

    func placeOrder(items: [String]) -> Bool {
        let userId = AuthManager.shared.currentUserId
        guard !userId.isEmpty else {
            AnalyticsManager.shared.track(event: "order_failed_no_user")
            return false
        }

        let total = PricingEngine.shared.computeTotal(for: items)
        UserDefaults.standard.set(total, forKey: "lastOrderTotal")
        AnalyticsManager.shared.track(event: "order_placed")
        return true
    }

    func lastOrderTotal() -> Double {
        return UserDefaults.standard.double(forKey: "lastOrderTotal")
    }
}
