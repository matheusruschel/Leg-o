import Foundation

struct User {
    let name: String
    let email: String

    init(name: String, email: String) {
        self.name = name
        self.email = email
    }

    func isValidEmail() -> Bool {
        return email.contains("@") && email.contains(".")
    }

    func displayName() -> String {
        return "\(name) <\(email)>"
    }
}
