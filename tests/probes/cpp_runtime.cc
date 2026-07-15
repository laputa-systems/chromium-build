#include <cstdlib>
#include <atomic>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <typeinfo>
#include <utility>
#include <vector>

struct Base {
    virtual ~Base() = default;
};

struct Derived final : Base {
    int value = 42;
};

thread_local int thread_value = 9;
std::atomic<int> observed_thread_value{0};

static int unwind(int depth) {
    if (depth == 0) {
        throw std::runtime_error("probe");
    }
    return unwind(depth - 1);
}

int main() {
    std::vector<std::string> values{"musl", "libc++", "llvm"};
    if (values[1] != "libc++") {
        return 1;
    }

    std::unique_ptr<Base> object = std::make_unique<Derived>();
    auto *derived = dynamic_cast<Derived *>(object.get());
    if (derived == nullptr || derived->value != 42) {
        return 2;
    }
    const std::type_info &object_type = typeid(*derived);
    if (object_type != typeid(Derived)) {
        return 2;
    }

    bool caught = false;
    try {
        unwind(8);
    } catch (const std::runtime_error &error) {
        caught = std::string(error.what()) == "probe";
    }
    if (!caught) {
        return 3;
    }

    std::thread thread([] {
        thread_value += 1;
        observed_thread_value.store(thread_value);
    });
    thread.join();
    if (observed_thread_value.load() != 10 || thread_value != 9) {
        return 4;
    }
    std::cout << "cpp-runtime-ok " << values.front() << '\n';
    return EXIT_SUCCESS;
}
