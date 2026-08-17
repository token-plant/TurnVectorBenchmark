#include <chrono>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

#include "mlx/export.h"
#include "mlx/mlx.h"

namespace mx = mlx::core;
using Clock = std::chrono::steady_clock;

struct Args {
  std::string graph;
  std::string output;
  std::string phase;
  int batch;
  int shape;
  int layers;
  int kv_heads;
  int head_dim;
  int vocab_size;
  int warmup;
  int iterations;
  int seed;
};

Args parse(int argc, char** argv) {
  if (argc != 13) {
    throw std::runtime_error(
        "usage: cpp_direct_oracle graph output phase batch shape layers "
        "kv_heads head_dim vocab_size warmup iterations seed");
  }
  return {argv[1], argv[2], argv[3], std::stoi(argv[4]), std::stoi(argv[5]),
          std::stoi(argv[6]), std::stoi(argv[7]), std::stoi(argv[8]),
          std::stoi(argv[9]), std::stoi(argv[10]), std::stoi(argv[11]),
          std::stoi(argv[12])};
}

mx::array tokens(int batch, int length, int vocab_size, int seed) {
  if (vocab_size <= 1024) {
    throw std::runtime_error("model vocabulary is too small");
  }
  std::vector<int32_t> values(batch * length);
  for (int row = 0; row < batch; ++row) {
    for (int column = 0; column < length; ++column) {
      const auto index = static_cast<size_t>(row * length + column);
      const auto value = static_cast<int64_t>(seed) +
                         static_cast<int64_t>(row) * 104729 +
                         static_cast<int64_t>(column) * 8191;
      values[index] = static_cast<int32_t>(
          value % static_cast<int64_t>(vocab_size - 1024) + 1024);
    }
  }
  return mx::array(values.begin(), {batch, length}, mx::int32);
}

mx::array empty_cache(const Args& args) {
  return mx::zeros({args.batch, args.kv_heads, 0, args.head_dim}, mx::bfloat16);
}

mx::Args make_prefill_inputs(const Args& args) {
  mx::Args inputs{tokens(args.batch, args.shape, args.vocab_size, args.seed)};
  for (int layer = 0; layer < args.layers; ++layer) {
    inputs.push_back(empty_cache(args));
    inputs.push_back(empty_cache(args));
  }
  return inputs;
}

void write_array(std::ofstream& output, const mx::array& value) {
  auto fp32 = mx::contiguous(mx::astype(value, mx::float32));
  mx::eval(fp32);
  output.write(reinterpret_cast<const char*>(fp32.data<float>()), fp32.nbytes());
  if (!output) {
    throw std::runtime_error("failed to write normalized output");
  }
}

int main(int argc, char** argv) try {
  const auto args = parse(argc, argv);
  if (args.phase != "decode" && args.phase != "prefill") {
    throw std::runtime_error("phase must be decode or prefill");
  }
  if (args.batch <= 0 || args.shape <= 0 || args.layers <= 0 ||
      args.kv_heads <= 0 || args.head_dim <= 0 || args.iterations <= 0) {
    throw std::runtime_error("shape and iteration arguments must be positive");
  }
  if (args.warmup < 0 || args.seed < 0) {
    throw std::runtime_error("warmup and seed arguments must be non-negative");
  }

  auto turn = mx::import_function(args.graph);
  mx::Args turn_inputs;
  if (args.phase == "decode") {
    // Qualification Decode always starts from a real Prefill result. This setup
    // is outside the measured region; synthetic zero KV is not accepted here.
    auto prefill_outputs = turn(make_prefill_inputs(args));
    if (prefill_outputs.size() != static_cast<size_t>(3 + args.layers * 2)) {
      throw std::runtime_error("unexpected Prefill output count");
    }
    mx::eval(prefill_outputs);
    turn_inputs.push_back(
        tokens(args.batch, 1, args.vocab_size, args.seed + 17));
    turn_inputs.insert(turn_inputs.end(), prefill_outputs.begin() + 3, prefill_outputs.end());
  } else {
    turn_inputs = make_prefill_inputs(args);
  }
  mx::eval(turn_inputs);

  std::ofstream samples(args.output + ".csv");
  if (!samples.is_open()) {
    throw std::runtime_error("failed to open latency samples output");
  }
  samples << "iteration,wall_us\n";
  for (int iteration = 0; iteration < args.warmup + args.iterations; ++iteration) {
    const auto start = Clock::now();
    auto outputs = turn(turn_inputs);
    if (outputs.size() != static_cast<size_t>(3 + args.layers * 2)) {
      throw std::runtime_error("unexpected Turn output count");
    }
    mx::eval(outputs);
    const auto end = Clock::now();
    if (iteration == args.warmup) {
      std::ofstream logits(args.output + ".logits.f32", std::ios::binary);
      write_array(logits, outputs[2]);
      std::ofstream kv(args.output + ".kv.f32", std::ios::binary);
      for (size_t index = 3; index < outputs.size(); ++index) {
        write_array(kv, outputs[index]);
      }
    }
    if (iteration >= args.warmup) {
      const auto elapsed =
          std::chrono::duration<double, std::micro>(end - start).count();
      samples << (iteration - args.warmup) << ',' << elapsed << '\n';
    }
  }
  samples.flush();
  if (!samples) {
    throw std::runtime_error("failed to write latency samples");
  }
  return 0;
} catch (const std::exception& error) {
  std::cerr << error.what() << '\n';
  return 1;
}
