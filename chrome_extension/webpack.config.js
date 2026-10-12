const path = require("path");
const webpack = require("webpack");
const CopyPlugin = require("copy-webpack-plugin");
const MiniCssExtractPlugin = require("mini-css-extract-plugin");

module.exports = (env, argv) => {
  const isDev = argv.mode === "development";

  return {
    entry: {
      background: "./src/background/service-worker.ts",
      "content-meet": "./src/content/meet/detector.ts",
      "content-zoom": "./src/content/zoom/detector.ts",
      "content-teams": "./src/content/teams/detector.ts",
      "content-teams-page": "./src/content/teams/page-interceptor.ts",
      "content-memoir-app": "./src/content/memoir-app/auth-sync.ts",
      popup: "./src/popup/index.tsx",
      offscreen: "./src/offscreen/offscreen.ts",
    },
    output: {
      filename: "[name].js",
      path: path.resolve(__dirname, "dist"),
      clean: true,
    },
    module: {
      rules: [
        {
          test: /\.tsx?$/,
          use: "ts-loader",
          exclude: /node_modules/,
        },
        {
          // Content script CSS — imported as raw string for Shadow DOM injection
          test: /\.css$/,
          include: path.resolve(__dirname, "src/content"),
          type: "asset/source",
        },
        {
          // Popup CSS — extracted to separate .css file
          test: /\.css$/,
          exclude: path.resolve(__dirname, "src/content"),
          use: [MiniCssExtractPlugin.loader, "css-loader"],
        },
      ],
    },
    resolve: {
      extensions: [".ts", ".tsx", ".js", ".jsx"],
    },
    plugins: [
      new webpack.DefinePlugin({
        __MEMOIR_DEV__: JSON.stringify(isDev),
      }),
      new MiniCssExtractPlugin({ filename: "[name].css" }),
      new CopyPlugin({
        patterns: [
          { from: isDev ? "manifest.json" : "manifest.prod.json", to: "manifest.json" },
          { from: "src/popup/index.html", to: "popup.html" },
          { from: "src/offscreen/offscreen.html", to: "offscreen.html" },
          { from: "assets/icons", to: "icons", noErrorOnMissing: true },
          { from: "assets/logo-icon.svg", to: "logo-icon.svg", noErrorOnMissing: true },
          { from: "assets/logo.svg", to: "logo.svg", noErrorOnMissing: true },
        ],
      }),
    ],
    devtool: isDev ? "inline-source-map" : false,
    optimization: {
      minimize: !isDev,
    },
  };
};
