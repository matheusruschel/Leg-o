import XCTest
@testable import MyApp

final class NetworkServiceTests: XCTestCase {
    private var sut: NetworkService!
    private var mockSession: MockURLSession!

    override func setUp() {
        super.setUp()
        mockSession = MockURLSession()
        sut = NetworkService(session: mockSession)
    }

    override func tearDown() {
        sut = nil
        mockSession = nil
        super.tearDown()
    }

    func test_fetchUser_validId_returnsUser() {
        let expectation = expectation(description: "fetchUser completes")
        mockSession.dataResult = .success(Data("{\"id\":1}".utf8))

        sut.fetchUser(id: 1) { result in
            switch result {
            case .success(let user):
                XCTAssertEqual(user.id, 1)
            case .failure:
                XCTFail("expected success")
            }
            expectation.fulfill()
        }

        wait(for: [expectation], timeout: 5.0)
    }
}

final class MockURLSession: URLSessionProtocol {
    var dataResult: Result<Data, Error>?
    var dataCalled = false

    func data(for request: URLRequest, completion: @escaping (Result<Data, Error>) -> Void) {
        dataCalled = true
        if let result = dataResult {
            completion(result)
        }
    }
}
