import pluginWebc from "@11ty/eleventy-plugin-webc";

export default function (eleventyConfig) {
  eleventyConfig.addPlugin(pluginWebc, {
    components: "src/_components/**/*.webc",
  });

  eleventyConfig.addPassthroughCopy("src/favicon.ico");
  eleventyConfig.addPassthroughCopy("src/**/*.js");
  eleventyConfig.addPassthroughCopy("src/**/*.css");
  eleventyConfig.addPassthroughCopy("src/fonts");
  eleventyConfig.addPassthroughCopy("src/images");
  eleventyConfig.addPassthroughCopy({
    "node_modules/maplibre-gl/dist/maplibre-gl.js": "vendor/maplibre-gl.js",
    "node_modules/maplibre-gl/dist/maplibre-gl.css": "vendor/maplibre-gl.css",
  });

  eleventyConfig.addWatchTarget("src/_components/");

  eleventyConfig.setServerOptions({
    domDiff: false,
    hostname: "127.0.0.1",
    middleware: [
      function (req, _res, next) {
        // Rewrite /account/imports/<job_id> to /account/imports/job/index.html
        if (req.url.match(/^\/account\/imports\/[^/]+\/?$/)) {
          req.url = "/account/imports/job/index.html";
        }
        // Rewrite /profile/<did>/activity/<rkey> to /profile/activity/index.html
        else if (req.url.match(/^\/profile\/[^/]+\/activity\/[^/]+\/?$/)) {
          req.url = "/profile/activity/index.html";
        }
        // Rewrite /profile/<handle> to /profile/index.html
        else if (req.url.match(/^\/profile\/[^/]+\/?$/)) {
          req.url = "/profile/index.html";
        }
        next();
      },
    ],
  });

  return {
    dir: {
      input: "src",
      output: "_site",
    },
  };
}
