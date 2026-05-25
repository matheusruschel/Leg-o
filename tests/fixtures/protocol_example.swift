import Foundation

protocol PaymentGateway {
    func authorize(amount: Decimal) throws -> String
    func capture(transactionId: String) throws
    func refund(transactionId: String, amount: Decimal) throws
}

protocol PaymentLogger {
    func log(_ message: String)
}

class PaymentProcessor {

    private let gateway: PaymentGateway
    private let logger: PaymentLogger
    private let maxAmount: Decimal

    init(gateway: PaymentGateway, logger: PaymentLogger, maxAmount: Decimal) {
        self.gateway = gateway
        self.logger = logger
        self.maxAmount = maxAmount
    }

    func process(amount: Decimal) throws -> String {
        guard amount > 0 else {
            throw PaymentError.invalidAmount
        }
        guard amount <= maxAmount else {
            throw PaymentError.amountExceedsLimit
        }
        logger.log("Authorizing \(amount)")
        let txId = try gateway.authorize(amount: amount)
        try gateway.capture(transactionId: txId)
        return txId
    }

    func refund(transactionId: String, amount: Decimal) throws {
        logger.log("Refunding \(amount) for \(transactionId)")
        try gateway.refund(transactionId: transactionId, amount: amount)
    }
}

enum PaymentError: Error {
    case invalidAmount
    case amountExceedsLimit
}
