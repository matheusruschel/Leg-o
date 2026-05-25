import UIKit

class ProfileViewController: UIViewController, UITableViewDataSource {

    @IBOutlet weak var tableView: UITableView!
    @IBOutlet weak var nameLabel: UILabel!

    private let networkService: NetworkServiceProtocol
    private var users: [User] = []
    private let defaults = UserDefaults.standard

    init(networkService: NetworkServiceProtocol) {
        self.networkService = networkService
        super.init(nibName: nil, bundle: nil)
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        AnalyticsManager.shared.track(event: "profile_view")
        loadUsers()
    }

    func loadUsers() {
        networkService.fetchUsers { [weak self] result in
            switch result {
            case .success(let users):
                self?.users = users
                self?.tableView.reloadData()
            case .failure:
                self?.users = []
            }
        }
    }

    func cachedUserName() -> String? {
        return defaults.string(forKey: "lastUserName")
    }

    func tableView(_ tableView: UITableView, numberOfRowsInSection section: Int) -> Int {
        return users.count
    }

    func tableView(_ tableView: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        let cell = tableView.dequeueReusableCell(withIdentifier: "Cell", for: indexPath)
        cell.textLabel?.text = users[indexPath.row].displayName()
        return cell
    }
}
