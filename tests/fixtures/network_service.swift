import Foundation

protocol NetworkServiceProtocol {
    func fetchUsers(completion: @escaping (Result<[User], Error>) -> Void)
}

class NetworkService: NetworkServiceProtocol {

    private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
    }

    func fetchUsers(completion: @escaping (Result<[User], Error>) -> Void) {
        let url = URL(string: "https://example.com/users")!
        let task = session.dataTask(with: url) { data, _, error in
            if let error = error {
                completion(.failure(error))
                return
            }
            completion(.success([]))
        }
        task.resume()
    }
}
